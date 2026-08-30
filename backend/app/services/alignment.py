from __future__ import annotations

import copy
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from uuid import UUID, uuid5

from pykakasi import kakasi

from app.services.transcription import Transcript

_CONVERTER = kakasi()
_SMALL_KANA = frozenset("ゃゅょぁぃぅぇぉゎゕゖ")


class AlignmentQualityError(ValueError):
    pass


def normalize_reading(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    hiragana = "".join(item["hira"] for item in _CONVERTER.convert(normalized))
    return "".join(
        char.lower() for char in hiragana
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "S"))
    )


def split_moras(reading: str) -> list[str]:
    result: list[str] = []
    for char in reading:
        if char.isspace() or unicodedata.category(char).startswith(("P", "S")):
            continue
        if char in _SMALL_KANA and result:
            result[-1] += char
        else:
            result.append(char)
    return result


@dataclass(frozen=True)
class TargetMora:
    line_id: str
    unit_id: str
    reading: str


@dataclass(frozen=True)
class ObservedMora:
    reading: str
    start_ms: int
    end_ms: int
    confidence: float


def _split_text_units(document: dict, line_ids: set[str]) -> None:
    if document.get("lyrics", {}).get("source_type") != "text":
        return
    for line in document["lyrics"]["lines"]:
        if line["id"] not in line_ids or len(line.get("units", [])) != 1:
            continue
        original = line["units"][0]
        surface = original.get("surface", "")
        if len(surface) <= 1:
            continue
        namespace = UUID(original["id"])
        units = []
        for index, char in enumerate(surface):
            unit = {**original}
            unit["id"] = original["id"] if index == 0 else str(uuid5(namespace, f"analysis:{index}:{char}"))
            unit["surface"] = char
            unit["start_ms"] = None
            unit["end_ms"] = None
            unit["timing_source"] = "interpolated"
            unit["timing_confidence"] = None
            units.append(unit)
        line["units"] = units


def _targets(lines: list[dict], unit_ids: set[str]) -> tuple[list[TargetMora], list[str]]:
    targets: list[TargetMora] = []
    unit_order: list[str] = []
    for line in lines:
        eligible = [unit for unit in line.get("units", []) if not unit_ids or unit["id"] in unit_ids]
        unit_order.extend(unit["id"] for unit in eligible)
        contextual = (
            len(eligible) > 1
            and all(len(unit.get("surface", "")) == 1 and not unit.get("ruby") for unit in eligible)
        )
        if contextual:
            readable_units = [unit for unit in eligible if normalize_reading(unit.get("surface", ""))]
            moras = split_moras(normalize_reading("".join(unit.get("surface", "") for unit in eligible)))
            for index, mora in enumerate(moras):
                owner = readable_units[min(len(readable_units) - 1, index * len(readable_units) // max(1, len(moras)))]
                targets.append(TargetMora(line["id"], owner["id"], mora))
            continue
        for unit in eligible:
            if unit_ids and unit["id"] not in unit_ids:
                continue
            for mora in split_moras(normalize_reading(unit.get("ruby") or unit.get("surface", ""))):
                targets.append(TargetMora(line["id"], unit["id"], mora))
    return targets, unit_order


def _observations(transcript: Transcript) -> list[ObservedMora]:
    result: list[ObservedMora] = []
    for segment in transcript.segments:
        for word in segment.words:
            moras = split_moras(normalize_reading(word.text))
            span = max(0, word.end_ms - word.start_ms)
            for index, mora in enumerate(moras):
                result.append(ObservedMora(
                    mora,
                    word.start_ms + span * index // max(1, len(moras)),
                    word.start_ms + span * (index + 1) // max(1, len(moras)),
                    word.confidence,
                ))
    return result


def _timings(targets: list[TargetMora], observations: list[ObservedMora], start_ms: int, end_ms: int) -> tuple[list[tuple[int, int, float, bool]], float]:
    matcher = SequenceMatcher(None, [item.reading for item in targets], [item.reading for item in observations], autojunk=False)
    matches: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            matches[block.a + offset] = block.b + offset
    ratio = len(matches) / max(1, len(targets))
    if not observations:
        raise AlignmentQualityError("Whisper 没有返回逐词时间戳")
    if ratio < 0.15:
        raise AlignmentQualityError("识别文本与歌词匹配度过低，已保留原时间轴")
    assigned: list[tuple[int, int, float, bool] | None] = [None] * len(targets)
    for target_index, observed_index in matches.items():
        item = observations[observed_index]
        assigned[target_index] = (item.start_ms, item.end_ms, item.confidence, True)
    cursor = 0
    while cursor < len(assigned):
        if assigned[cursor] is not None:
            cursor += 1
            continue
        gap_start = cursor
        while cursor < len(assigned) and assigned[cursor] is None:
            cursor += 1
        count = cursor - gap_start
        left = assigned[gap_start - 1][1] if gap_start else start_ms
        right = assigned[cursor][0] if cursor < len(assigned) else end_ms
        right = max(left, right)
        for offset in range(count):
            assigned[gap_start + offset] = (
                left + (right - left) * offset // count,
                left + (right - left) * (offset + 1) // count,
                0.0,
                False,
            )
    normalized: list[tuple[int, int, float, bool]] = []
    previous_end = start_ms
    for raw in assigned:
        assert raw is not None
        item_start = max(previous_end, raw[0])
        item_end = max(item_start, raw[1])
        normalized.append((item_start, item_end, raw[2], raw[3]))
        previous_end = item_end
    return normalized, ratio


def align_document(
    source_document: dict,
    transcript: Transcript,
    *,
    line_ids: list[str] | None = None,
    unit_ids: list[str] | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    preserve_line_anchors: bool = True,
    overwrite_locked: bool = False,
) -> tuple[dict, dict]:
    document = copy.deepcopy(source_document)
    all_lines = document.get("lyrics", {}).get("lines", [])
    selected_line_ids = set(line_ids or [line["id"] for line in all_lines])
    selected_lines = [line for line in all_lines if line["id"] in selected_line_ids]
    if not selected_lines:
        raise AlignmentQualityError("没有可识别的歌词范围")
    _split_text_units(document, selected_line_ids)
    selected_lines = [line for line in document["lyrics"]["lines"] if line["id"] in selected_line_ids]
    targets, unit_order = _targets(selected_lines, set(unit_ids or []))
    if not targets:
        raise AlignmentQualityError("所选歌词不包含可对齐的文字")
    observations = _observations(transcript)
    lower = start_ms if start_ms is not None else min((item.start_ms for item in observations), default=0)
    upper = end_ms if end_ms is not None else max((item.end_ms for item in observations), default=lower + 1000)
    aligned, overall_confidence = _timings(targets, observations, lower, upper)
    by_unit: dict[str, list[tuple[int, int, float, bool]]] = {}
    for target, timing in zip(targets, aligned):
        by_unit.setdefault(target.unit_id, []).append(timing)
    previous_end = lower
    for line in selected_lines:
        original_anchor = line.get("anchor_ms")
        updated_units = []
        for unit in line.get("units", []):
            timings = by_unit.get(unit["id"])
            if not timings:
                if unit["id"] in unit_order and (overwrite_locked or not unit.get("locked")):
                    unit["start_ms"] = previous_end
                    unit["end_ms"] = previous_end
                    unit["timing_source"] = "interpolated"
                    unit["timing_confidence"] = 0.0
                updated_units.append(unit)
                continue
            if overwrite_locked or not unit.get("locked"):
                unit["start_ms"] = timings[0][0]
                unit["end_ms"] = timings[-1][1]
                matched = sum(1 for item in timings if item[3])
                probability = sum(item[2] for item in timings if item[3]) / max(1, matched)
                confidence = matched / len(timings) * probability
                unit["timing_confidence"] = round(confidence, 4)
                unit["timing_source"] = "whisper_matched" if matched else "interpolated"
                previous_end = unit["end_ms"]
            updated_units.append(unit)
        line["units"] = updated_units
        timed = [unit for unit in updated_units if unit.get("start_ms") is not None and unit.get("end_ms") is not None]
        if timed:
            predicted_start = min(unit["start_ms"] for unit in timed)
            predicted_end = max(unit["end_ms"] for unit in timed)
            if preserve_line_anchors and original_anchor is not None and line.get("end_ms") is not None:
                anchor_end = max(original_anchor, line["end_ms"])
                span = max(1, predicted_end - predicted_start)
                for unit in timed:
                    if unit.get("locked") and not overwrite_locked:
                        continue
                    unit["start_ms"] = round(original_anchor + (unit["start_ms"] - predicted_start) * (anchor_end - original_anchor) / span)
                    unit["end_ms"] = round(original_anchor + (unit["end_ms"] - predicted_start) * (anchor_end - original_anchor) / span)
            line["start_ms"] = min(unit["start_ms"] for unit in timed)
            line["end_ms"] = max(unit["end_ms"] for unit in timed)
            line["timing_source"] = "whisper_matched"
            line["timing_precision"] = "unit"
    low_confidence = sum(
        1 for line in selected_lines for unit in line.get("units", [])
        if unit.get("timing_confidence") is not None and unit["timing_confidence"] < 0.55
    )
    return document, {
        "confidence": round(overall_confidence, 4),
        "selected_lines": len(selected_lines),
        "selected_units": len(unit_order),
        "low_confidence_units": low_confidence,
    }
