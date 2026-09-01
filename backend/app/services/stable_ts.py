from __future__ import annotations

import copy
import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from app.services.alignment import AlignmentQualityError, normalize_reading
from app.services.transcription import Transcript


class StableTSAlignmentError(AlignmentQualityError):
    pass


def rough_line_ranges(document: dict, transcript: Transcript) -> dict[str, tuple[int, int]]:
    """Find coarse line ranges without changing lyric units or exposing stable-ts objects."""
    lines = document.get("lyrics", {}).get("lines", [])
    segments = transcript.segments
    if not lines or not segments:
        raise StableTSAlignmentError("没有可用于粗定位的 Whisper segment")
    targets = [
        normalize_reading("".join(str(unit.get("ruby") or unit.get("surface", "")) for unit in line.get("units", [])))
        for line in lines
    ]
    scores = [
        [SequenceMatcher(None, target, normalize_reading(segment.text), autojunk=False).ratio() for segment in segments]
        for target in targets
    ]

    # Solve the ordered assignment globally. Greedy matching is vulnerable to
    # repeated chorus lines and can jump to a later, equally similar segment.
    # Reusing a segment is intentional: Whisper may merge several lyric lines.
    dp = [[float("-inf")] * len(segments) for _ in lines]
    previous: list[list[int | None]] = [[None] * len(segments) for _ in lines]
    for segment_index in range(len(segments)):
        dp[0][segment_index] = scores[0][segment_index]
    for line_index in range(1, len(lines)):
        for segment_index in range(len(segments)):
            candidates = range(segment_index + 1)
            best_previous = max(candidates, key=lambda index: (dp[line_index - 1][index], -index))
            dp[line_index][segment_index] = dp[line_index - 1][best_previous] + scores[line_index][segment_index]
            previous[line_index][segment_index] = best_previous
    chosen = [0] * len(lines)
    chosen[-1] = max(range(len(segments)), key=lambda index: (dp[-1][index], -index))
    for line_index in range(len(lines) - 1, 0, -1):
        chosen[line_index - 1] = previous[line_index][chosen[line_index]] or 0
    result = {line["id"]: (segments[index].start_ms, segments[index].end_ms) for line, index in zip(lines, chosen)}
    return result


class StableTSAligner:
    """Map official stable-ts results into Nicokara's serializable timing fields."""

    def __init__(self, model_provider: Callable[[], Any]) -> None:
        self.model_provider = model_provider

    def _model(self) -> Any:
        try:
            return self.model_provider()
        except Exception as exc:
            raise StableTSAlignmentError(f"stable-ts 模型加载失败：{exc}") from exc

    @staticmethod
    def _save_result(result: Any, path: Path) -> None:
        """Persist a reloadable result without stable-ts's stale ori_dict."""
        result.language = "ja"
        path.parent.mkdir(parents=True, exist_ok=True)
        to_dict = getattr(result, "to_dict", None)
        if callable(to_dict):
            data = to_dict(keep_orig=False)
            data["language"] = "ja"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        result.save_as_json(str(path))

    def global_align(
        self,
        source_document: dict,
        audio: Path,
        *,
        token_step: int = 100,
        result_path: Path | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[dict, dict]:
        try:
            import stable_whisper
        except ImportError as exc:
            raise StableTSAlignmentError("stable-ts 尚未安装") from exc

        document = copy.deepcopy(source_document)
        lines = document.get("lyrics", {}).get("lines", [])
        if not lines:
            raise StableTSAlignmentError("没有可全局对齐的歌词")
        model = self._model()
        if progress_callback:
            progress_callback(0.02, "正在使用 AI 注音歌词进行全曲强制对齐")

        readings = [
            "".join(str(unit.get("ruby") or unit.get("surface") or "") for unit in line.get("units", []))
            for line in lines
        ]
        try:
            full_result = stable_whisper.alignment.align(
                model,
                str(audio),
                "\n".join(readings),
                language="ja",
                original_split=True,
                token_step=token_step,
                verbose=None,
                suppress_silence=True,
                progress_callback=(
                    (lambda current, total: progress_callback(0.02 + 0.94 * float(current) / max(0.001, float(total)), "stable-ts 正在对齐 AI 注音完整歌词"))
                    if progress_callback else None
                ),
            )
            full_segments = list(getattr(full_result, "segments", []) or [])
            if len(full_segments) != len(lines):
                raise StableTSAlignmentError(
                    f"stable-ts 全曲对齐返回 {len(full_segments)} 行，预期 {len(lines)} 行"
                )
            full_result.language = "ja"
        except StableTSAlignmentError:
            raise
        except Exception as exc:
            raise StableTSAlignmentError(f"stable-ts 全局对齐失败：{exc}") from exc
        for line, segment in zip(lines, full_segments):
            line["start_ms"] = round(float(getattr(segment, "start", 0)) * 1000)
            line["end_ms"] = round(float(getattr(segment, "end", 0)) * 1000)
            line["timing_source"] = "stable_ts_global"
            line["timing_precision"] = "line"
        if result_path is not None:
            try:
                self._save_result(full_result, result_path)
            except Exception as exc:
                raise StableTSAlignmentError(f"stable-ts 全局结果保存失败：{exc}") from exc
        if progress_callback:
            progress_callback(1.0, "stable-ts 全曲行级对齐完成")
        return document, {
            "engine": "stable-ts",
            "granularity": "line",
            "aligned_lines": len(lines),
            "token_step": token_step,
            "suppress_silence": True,
            "result_artifact": result_path.name if result_path is not None else None,
        }

    def align_words(
        self,
        source_document: dict,
        audio: Path,
        *,
        line_ids: list[str] | None = None,
        overwrite_locked: bool = False,
        segment_padding_seconds: float = 2.0,
        time_offset_ms: int = 0,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[dict, dict]:
        try:
            import stable_whisper
        except ImportError as exc:
            raise StableTSAlignmentError("stable-ts 尚未安装") from exc

        document = copy.deepcopy(source_document)
        lines = document.get("lyrics", {}).get("lines", [])
        selected = set(line_ids or [line["id"] for line in lines])
        selected_lines = [line for line in lines if line["id"] in selected]
        if not selected_lines:
            raise StableTSAlignmentError("没有可精修的歌词行")
        model = self._model()
        batches: list[tuple[list[dict[str, Any]], list[tuple[dict, dict]]]] = []
        owners: list[tuple[dict, dict]] = []
        for line in selected_lines:
            line_start, line_end = line.get("start_ms"), line.get("end_ms")
            if line_start is None or line_end is None or int(line_end) <= int(line_start):
                raise StableTSAlignmentError("精修前必须先完成 stable-ts 全局对齐")
            line_start, line_end = int(line_start) - time_offset_ms, int(line_end) - time_offset_ms
            padded_start = max(0.0, line_start / 1000 - segment_padding_seconds)
            padded_end = line_end / 1000 + segment_padding_seconds
            units = [unit for unit in line.get("units", []) if str(unit.get("surface", ""))]
            if not units:
                continue
            span = max(0.001, padded_end - padded_start)
            line_inputs: list[dict[str, Any]] = []
            line_owners: list[tuple[dict, dict]] = []
            for index, unit in enumerate(units):
                # Rebuild sorted unit windows from the line range. Padding
                # lets stable-ts search beyond the global line boundaries.
                unit_start = padded_start + span * index / len(units)
                unit_end = padded_start + span * (index + 1) / len(units)
                reading = str(unit.get("ruby") or unit.get("surface") or "").strip()
                line_inputs.append({"start": unit_start, "end": unit_end, "text": reading})
                line_owners.append((line, unit))
            batches.append((line_inputs, line_owners))
            owners.extend(line_owners)

        if not batches:
            raise StableTSAlignmentError("所选歌词没有可精对齐的词或短语")
        applied = 0
        low_confidence = 0
        for batch_index, (line_inputs, line_owners) in enumerate(batches):
            try:
                result = stable_whisper.alignment.align_words(
                    model,
                    str(audio),
                    line_inputs,
                    language="ja",
                    verbose=None,
                    suppress_silence=True,
                    normalize_text=True,
                    inplace=False,
                )
            except Exception as exc:
                raise StableTSAlignmentError(f"stable-ts 对齐失败（第 {batch_index + 1} 行）：{exc}") from exc
            stable_segments = list(getattr(result, "segments", []) or [])
            for index, (line, unit) in enumerate(line_owners):
                if unit.get("locked") and not overwrite_locked:
                    continue
                segment = stable_segments[index] if index < len(stable_segments) else None
                words = list(getattr(segment, "words", []) or []) if segment else []
                timed = words or ([segment] if segment else [])
                if not timed:
                    continue
                starts = [float(getattr(item, "start", 0)) for item in timed]
                ends = [float(getattr(item, "end", 0)) for item in timed]
                start_ms = time_offset_ms + round(min(starts) * 1000)
                end_ms = max(start_ms, time_offset_ms + round(max(ends) * 1000))
                probabilities = [float(getattr(item, "probability", 0.85) or 0.85) for item in timed]
                confidence = round(sum(probabilities) / len(probabilities), 4)
                unit["start_ms"] = start_ms
                unit["end_ms"] = end_ms
                unit["timing_source"] = "stable_ts"
                unit["timing_confidence"] = confidence
                line["timing_source"] = "stable_ts"
                line["timing_precision"] = "phrase"
                applied += 1
                if confidence < 0.55:
                    low_confidence += 1
            if progress_callback:
                progress_callback(0.08 + 0.82 * (batch_index + 1) / len(batches), f"stable-ts 正在精修第 {batch_index + 1}/{len(batches)} 行")
        if progress_callback:
            progress_callback(0.92, "正在写回词/短语时间")
        for line in selected_lines:
            timed_units = [unit for unit in line.get("units", []) if unit.get("start_ms") is not None and unit.get("end_ms") is not None]
            if timed_units:
                line["start_ms"] = min(int(unit["start_ms"]) for unit in timed_units)
                line["end_ms"] = max(int(unit["end_ms"]) for unit in timed_units)
        if progress_callback:
            progress_callback(1.0, "stable-ts 词/短语级精对齐完成")
        return document, {
            "engine": "stable-ts",
            "granularity": "phrase",
            "selected_lines": len(selected_lines),
            "selected_units": len(owners),
            "applied_units": applied,
            "low_confidence_units": low_confidence,
            "segment_padding_seconds": segment_padding_seconds,
            "range_source": "single_line_audio_clip" if time_offset_ms else "stable_ts_global_with_segment_padding",
            "time_offset_ms": time_offset_ms,
        }
