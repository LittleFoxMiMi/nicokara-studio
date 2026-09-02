from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pykakasi import kakasi

from app.services.alignment import AlignmentQualityError
from app.services.fa_kara_text import normalize_language, phonetic_for_surface, tokenize_fa_kara


class FAKaraAlignmentError(AlignmentQualityError):
    pass


_KANA_CONVERTER = kakasi()
_KANJI_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff々]")
_MODEL_READING_RE = re.compile(r"[^a-zA-Z']+")


def _romaji(reading: str) -> str:
    """Convert an existing Japanese ruby/surface to MMS_FA's Latin alphabet."""
    converted = "".join(str(item.get("hepburn") or "") for item in _KANA_CONVERTER.convert(reading))
    return _MODEL_READING_RE.sub("", converted).lower()


def _unit_reading(unit: dict[str, Any], *, language: str = "jp") -> str:
    surface = str(unit.get("surface") or "")
    ruby = str(unit.get("ruby") or "").strip()
    alignment_reading = str(unit.get("alignment_reading") or "").strip()
    language = normalize_language(language)
    if language == "jp" and _KANJI_RE.search(surface) and not ruby:
        raise FAKaraAlignmentError("FA-Kara 需要已有 AI 注音：请先为含汉字的歌词生成 Ruby")
    if language == "jp":
        return _romaji(ruby) if ruby else alignment_reading or phonetic_for_surface(surface, language=language)
    return alignment_reading or phonetic_for_surface(surface, language=language)


def _groups(line: dict[str, Any], *, language: str = "jp") -> list[tuple[list[dict[str, Any]], str]]:
    language = normalize_language(language)
    units = [unit for unit in line.get("units", []) if str(unit.get("surface") or "")]
    result: list[tuple[list[dict[str, Any]], str]] = []
    index = 0
    while index < len(units):
        unit = units[index]
        try:
            span = max(1, int(unit.get("ruby_span") or 1))
        except (TypeError, ValueError):
            span = 1
        member_end = index + 1
        covered_chars = len(str(unit.get("surface") or ""))
        while covered_chars < span and member_end < len(units):
            covered_chars += len(str(units[member_end].get("surface") or ""))
            member_end += 1
        members = units[index:member_end]
        reading = str(unit.get("ruby") or "").strip() if language == "jp" else ""
        if not reading:
            readings = [_unit_reading(member, language=language) for member in members]
            reading = "".join(readings)
        if language == "jp":
            reading = _romaji(reading)
        if reading:
            result.append((members, reading))
        index += len(members)
    return result


def _prepare_chinese_units(lines: list[dict[str, Any]]) -> None:
    """Split Chinese surfaces into deterministic FA-Kara units without creating Ruby."""
    for line in lines:
        rebuilt: list[dict[str, Any]] = []
        for original in line.get("units", []):
            surface = str(original.get("surface") or "")
            tokens = tokenize_fa_kara(surface, language="cn")
            if not tokens:
                continue
            for index, (start, end, reading) in enumerate(tokens):
                unit = copy.deepcopy(original)
                unit["surface"] = surface[start:end]
                if index:
                    unit["id"] = str(uuid4())
                unit["ruby"] = None
                unit["ruby_2"] = None
                unit["ruby_source"] = "none"
                unit.pop("ruby_span", None)
                unit.pop("ruby_confidence", None)
                if reading:
                    unit["alignment_reading"] = reading
                else:
                    unit.pop("alignment_reading", None)
                start_ms, end_ms = original.get("start_ms"), original.get("end_ms")
                if start_ms is not None and end_ms is not None and surface:
                    duration = int(end_ms) - int(start_ms)
                    unit["start_ms"] = round(int(start_ms) + duration * start / len(surface))
                    unit["end_ms"] = round(int(start_ms) + duration * end / len(surface))
                rebuilt.append(unit)
        line["units"] = rebuilt


class FAKaraAligner:
    """FA-Kara adapter using the project's existing ruby data."""

    def __init__(self, model_provider: Callable[[], Any], *, model_name: str = "mms") -> None:
        self.model_provider = model_provider
        self.model_name = model_name

    def align(
        self,
        source_document: dict,
        audio: Path,
        *,
        line_ids: list[str] | None = None,
        overwrite_locked: bool = False,
        result_path: Path | None = None,
        time_offset_ms: int = 0,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[dict, dict]:
        try:
            import soundfile
            import torch
            import torchaudio
        except ImportError as exc:
            raise FAKaraAlignmentError("FA-Kara 运行时需要 soundfile、torch 和 torchaudio") from exc
        if not audio.is_file():
            raise FAKaraAlignmentError("FA-Kara 找不到人声音频，请先完成人声分离")
        document = copy.deepcopy(source_document)
        lines = document.get("lyrics", {}).get("lines", [])
        language = normalize_language(document.get("project", {}).get("language"))
        selected = set(line_ids or [line["id"] for line in lines])
        if language == "cn":
            _prepare_chinese_units([line for line in lines if line["id"] in selected])
        selected_lines = [line for line in lines if line["id"] in selected]
        if not selected_lines:
            raise FAKaraAlignmentError("没有可用于 FA-Kara 对齐的歌词行")
        targets: list[tuple[dict[str, Any], list[dict[str, Any]], str]] = []
        for line in selected_lines:
            for members, reading in _groups(line, language=language):
                targets.append((line, members, reading))
        if not targets:
            raise FAKaraAlignmentError("歌词没有可转换为罗马音的内容")
        if progress_callback:
            progress_callback(0.04, "正在加载 FA-Kara 对齐模型")
        model, bundle = self.model_provider()
        try:
            samples, sample_rate = soundfile.read(str(audio), dtype="float32", always_2d=True)
        except (OSError, RuntimeError) as exc:
            raise FAKaraAlignmentError(f"FA-Kara 无法读取人声音频：{exc}") from exc
        waveform = torch.from_numpy(samples.T.copy()).mean(0, keepdim=True)
        if sample_rate != int(bundle.sample_rate):
            waveform = torchaudio.functional.resample(waveform, sample_rate, int(bundle.sample_rate))
        tokenizer = bundle.get_tokenizer()
        aligner = bundle.get_aligner()
        token_text = [item[2] for item in targets]
        try:
            tokens = tokenizer(token_text)
            device = next(model.parameters()).device if hasattr(model, "parameters") else torch.device("cpu")
            with torch.inference_mode():
                emission, _ = model(waveform.to(device))
            spans = aligner(emission[0], tokens)
        except Exception as exc:
            raise FAKaraAlignmentError(f"FA-Kara CTC 对齐失败：{exc}") from exc
        frame_hop_samples = int(getattr(bundle, "frame_hop_samples", 320))
        frame_duration_ms = frame_hop_samples * 1000 / int(bundle.sample_rate)
        applied = 0
        locked = 0
        low_confidence = 0
        for target_index, (line, members, _) in enumerate(targets):
            target_spans = spans[target_index] if target_index < len(spans) else []
            if not target_spans:
                continue
            start_ms = time_offset_ms + round(float(target_spans[0].start) * frame_duration_ms)
            end_ms = max(start_ms, time_offset_ms + round(float(target_spans[-1].end) * frame_duration_ms))
            score_values = []
            for item in target_spans:
                raw_score = float(getattr(item, "score", 0.0))
                probability = math.exp(raw_score) if raw_score <= 0 else raw_score
                score_values.append(max(0.0, min(1.0, probability)))
            confidence = round(sum(score_values) / len(score_values), 4) if score_values else 0.0
            duration = max(0, end_ms - start_ms)
            weights = [max(1, len(str(member.get("surface") or ""))) for member in members]
            total_weight = sum(weights)
            cursor = start_ms
            for member_index, member in enumerate(members):
                next_cursor = end_ms if member_index == len(members) - 1 else start_ms + round(duration * sum(weights[: member_index + 1]) / total_weight)
                if member.get("locked") and not overwrite_locked:
                    locked += 1
                else:
                    member["start_ms"] = cursor
                    member["end_ms"] = max(cursor, next_cursor)
                    member["timing_source"] = "fa_kara"
                    member["timing_confidence"] = confidence
                    applied += 1
                    if confidence < 0.55:
                        low_confidence += 1
                cursor = next_cursor
            timed = [unit for unit in line.get("units", []) if unit.get("start_ms") is not None and unit.get("end_ms") is not None]
            if timed:
                line["start_ms"] = min(int(unit["start_ms"]) for unit in timed)
                line["end_ms"] = max(int(unit["end_ms"]) for unit in timed)
                line["timing_source"] = "fa_kara"
                line["timing_precision"] = "phrase"
            if progress_callback:
                progress_callback(0.08 + 0.86 * (target_index + 1) / len(targets), f"FA-Kara 正在对齐第 {target_index + 1}/{len(targets)} 个词组")
        if result_path is not None:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({"engine": "fa-kara", "model": self.model_name, "targets": len(targets)}, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress_callback:
            progress_callback(1.0, "FA-Kara 对齐完成")
        return document, {
            "engine": "fa-kara",
            "model": self.model_name,
            "granularity": "phrase",
            "selected_lines": len(selected_lines),
            "selected_targets": len(targets),
            "applied_units": applied,
            "skipped_locked": locked,
            "low_confidence_units": low_confidence,
            "result_artifact": result_path.name if result_path else None,
            "time_offset_ms": time_offset_ms,
        }
