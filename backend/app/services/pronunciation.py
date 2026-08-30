from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pykakasi import kakasi
from app.services.ai_client import AIClient

DEFAULT_SYSTEM_PROMPT = r'''你是日语卡拉 OK 歌词注音器。你的任务是只为歌词正文中的汉字或包含汉字的特殊表记生成日语假名读音，不得翻译、改写、纠正或删除歌词正文。

你必须只返回一个合法 JSON 对象，不得返回 Markdown 代码围栏、解释、前后缀或注释。固定顶层结构如下：
{
  "result": [
    {
      "line_index": 0,
      "raw": ["雨", "が", "降った"],
      "pronunciation": [
        [0, 1, "雨", "あめ"],
        [2, 3, "降", "ふ"]
      ]
    }
  ]
}

协议规则：
1. line_index 必须是输入歌词行的 0-based 行号，按输入顺序返回，不得新增、删除、重复或调换行。
2. raw 是对原始歌词行的分词数组；把 raw 数组元素直接拼接后，必须与原始歌词行完全一致。不得修改空白、标点、假名、拉丁字母或歌词正文。
3. pronunciation 的每项必须严格是 [start, end, surface, reading]。start/end 是原始歌词行的 Unicode 字符半开区间 [start,end)，不是 raw 下标。
4. surface 必须严格等于原始歌词在 [start,end) 范围内的文字；范围不得越界、不得重叠，按 start 升序排列。
5. reading 只填写实际日语读音，使用平假名为主；多汉字词、当て字和特殊读音作为整体返回，例如 昨日 -> きのう、九十九折 -> つづらおり。
6. 只为汉字或包含汉字的特殊表记返回 pronunciation；假名、助词和标点不要重复返回。没有注音时 pronunciation 返回 []。
7. 不要生成工程内部 id、时间、置信度或其他字段。'''
DEFAULT_USER_TEMPLATE = r'''请严格按照系统协议，为下面的完整歌词生成注音 JSON。歌词正文是唯一真值，Whisper 文本仅用于判断读音，不得据此修改歌词。

歌曲名：{{song_title}}
歌手：{{artist}}

输入歌词行（必须逐行保留 line_index 和 text）：
{{current_lines}}

Whisper 顺序参考（只有 segment_index 和 text，没有歌词行号，也没有可写回的时间）：
{{whisper_segments}}

再次检查：raw 拼接必须等于每行原文；pronunciation 必须使用 [start,end,surface,reading]；surface 必须匹配原文；只注音汉字；最后只输出 JSON。'''
READING_RE = re.compile(r"^[ぁ-ゖァ-ヺー・ゔヴーA-Za-z0-9'\- ]+$")


class PronunciationValidationError(ValueError):
    pass


@dataclass
class PronunciationSelection:
    line_ids: list[str]
    unit_ids: list[str]
    overwrite_policy: str = "unlocked_only"


def _contains_kanji(value: str) -> bool:
    return any("一" <= char <= "龯" for char in value)


def local_reading(surface: str) -> tuple[str | None, float]:
    if not surface or not _contains_kanji(surface):
        return None, 1.0
    converter = kakasi()
    pieces = converter.convert(surface)
    reading = "".join(item.get("hira", item.get("orig", "")) for item in pieces)
    return (reading or None), (0.72 if reading else 0.0)


def _selected_units(document: dict, selection: PronunciationSelection) -> list[tuple[dict, dict]]:
    line_ids = set(selection.line_ids)
    unit_ids = set(selection.unit_ids)
    selected: list[tuple[dict, dict]] = []
    for line in document.get("lyrics", {}).get("lines", []):
        if line_ids and line["id"] not in line_ids and not any(unit["id"] in unit_ids for unit in line.get("units", [])):
            continue
        for unit in line.get("units", []):
            if not unit_ids or unit["id"] in unit_ids or line["id"] in line_ids:
                selected.append((line, unit))
    return selected


def apply_local(document: dict, selection: PronunciationSelection, *, fallback: bool = False) -> tuple[dict, dict]:
    updated = deepcopy(document)
    applied = 0
    skipped_locked = 0
    low_confidence = 0
    for _, unit in _selected_units(updated, selection):
        if unit.get("locked") and selection.overwrite_policy != "all":
            skipped_locked += 1
            continue
        reading, confidence = local_reading(str(unit.get("surface", "")))
        if not reading:
            continue
        unit["ruby"] = reading
        unit["ruby_source"] = "local_fallback" if fallback else "local"
        unit["ruby_confidence"] = confidence
        if confidence < 0.55:
            low_confidence += 1
        applied += 1
    updated.setdefault("pronunciation", {})["last_run"] = {"mode": "local_fallback" if fallback else "local", "applied": applied}
    return updated, {"applied": applied, "skipped_locked": skipped_locked, "low_confidence": low_confidence, "source": "local_fallback" if fallback else "local"}


def render_prompt(template: str, *, song_title: str, artist: str, current_lines: list[dict[str, Any]], whisper_segments: list[dict[str, Any]]) -> str:
    values = {
        "song_title": song_title,
        "artist": artist,
        "previous_lines": "[]",
        "current_lines": json.dumps(current_lines, ensure_ascii=False),
        "next_lines": "[]",
        "local_tokens": "[]",
        "selected_tokens": "[]",
        "whisper_segments": json.dumps(whisper_segments, ensure_ascii=False),
    }
    # Templates use double-brace variables; leave unknown text untouched.
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def chunk_lines(current_lines: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    """Split on line boundaries, allowing an individual long line to exceed the limit."""
    limit = max(1, int(max_chars))
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    batch_chars = 0
    for line in current_lines:
        line_chars = len(str(line.get("text", "")))
        if batch and batch_chars + line_chars > limit:
            batches.append(batch)
            batch = []
            batch_chars = 0
        batch.append(line)
        batch_chars += line_chars
    if batch:
        batches.append(batch)
    return batches


def validate_ai_result(payload: Any, lines: list[dict], allowed_line_indices: set[int] | None = None, *, check_raw: bool = True) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise PronunciationValidationError("AI 响应缺少 result 数组")
    line_map = {index: line for index, line in enumerate(lines)}
    seen: set[int] = set()
    validated: list[dict] = []
    for item in payload["result"]:
        if not isinstance(item, dict) or not isinstance(item.get("line_index"), int):
            raise PronunciationValidationError("AI 返回了无效 line_index")
        index = item["line_index"]
        if index not in line_map or index in seen or (allowed_line_indices is not None and index not in allowed_line_indices):
            raise PronunciationValidationError("AI 返回了未知或重复歌词行")
        line = line_map[index]
        text = "".join(unit.get("surface", "") for unit in line.get("units", []))
        raw = item.get("raw")
        if check_raw and (not isinstance(raw, list) or "".join(str(part) for part in raw) != text):
            raise PronunciationValidationError(f"第 {index + 1} 行 raw 与歌词正文不一致")
        ranges = item.get("pronunciation", [])
        if not isinstance(ranges, list):
            raise PronunciationValidationError("pronunciation 必须是数组")
        last_end = -1
        checked = []
        for entry in ranges:
            if not isinstance(entry, list) or len(entry) != 4:
                raise PronunciationValidationError("注音项必须是 [start,end,surface,reading]")
            start, end, surface, reading = entry
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(text) or start < last_end:
                raise PronunciationValidationError("注音范围越界或重叠")
            if text[start:end] != surface or not isinstance(reading, str) or not READING_RE.match(reading):
                raise PronunciationValidationError("注音 surface 或 reading 不合法")
            checked.append([start, end, surface, reading])
            last_end = end
        validated.append({"line_index": index, "pronunciation": checked})
        seen.add(index)
    return validated


def validate_ai_result_partial(payload: Any, lines: list[dict], allowed_line_indices: set[int] | None = None) -> tuple[list[dict], set[int], set[int]]:
    """Keep valid rows, separating raw tokenization errors from pronunciation errors."""
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise PronunciationValidationError("AI 响应缺少 result 数组")
    line_map = {index: line for index, line in enumerate(lines)}
    valid: list[dict] = []
    invalid: set[int] = set()
    raw_mismatch: set[int] = set()
    seen: set[int] = set()
    for item in payload["result"]:
        if not isinstance(item, dict) or not isinstance(item.get("line_index"), int):
            raise PronunciationValidationError("AI 返回了无法定位的歌词行")
        index = item["line_index"]
        if index not in line_map or (allowed_line_indices is not None and index not in allowed_line_indices):
            raise PronunciationValidationError("AI 返回了当前批次之外的歌词行")
        if index in seen:
            invalid.add(index)
            valid = [proposal for proposal in valid if proposal["line_index"] != index]
            continue
        seen.add(index)
        try:
            valid.extend(validate_ai_result({"result": [item]}, lines, allowed_line_indices))
        except PronunciationValidationError:
            try:
                valid.extend(validate_ai_result({"result": [item]}, lines, allowed_line_indices, check_raw=False))
                raw_mismatch.add(index)
            except PronunciationValidationError:
                invalid.add(index)
    return valid, invalid, raw_mismatch


def apply_ai(document: dict, result: list[dict], selection: PronunciationSelection) -> tuple[dict, dict]:
    updated = deepcopy(document)
    lines = updated.get("lyrics", {}).get("lines", [])
    selected_lines = set(selection.line_ids)
    selected_units = set(selection.unit_ids)
    applied = 0
    skipped_locked = 0
    for proposal in result:
        line = lines[proposal["line_index"]]
        if (selected_lines or selected_units) and line["id"] not in selected_lines and not any(unit["id"] in selected_units for unit in line.get("units", [])):
            continue
        annotations = proposal["pronunciation"]
        line_selected = line["id"] in selected_lines
        cursor = 0
        next_units: list[dict] = []
        for original in line.get("units", []):
            surface = str(original.get("surface", ""))
            unit_start, unit_end = cursor, cursor + len(surface)
            cursor = unit_end
            if selected_units and not line_selected and original.get("id") not in selected_units:
                next_units.append(deepcopy(original))
                continue
            boundaries = {0, len(surface)}
            for start, end, _, _ in annotations:
                if start < unit_end and end > unit_start:
                    boundaries.add(max(0, start - unit_start))
                    boundaries.add(min(len(surface), end - unit_start))
            points = sorted(boundaries)
            for chunk_index, (left, right) in enumerate(zip(points, points[1:])):
                chunk = deepcopy(original)
                chunk["surface"] = surface[left:right]
                if chunk_index:
                    chunk["id"] = str(uuid4())
                if original.get("start_ms") is not None and original.get("end_ms") is not None and surface:
                    span = original["end_ms"] - original["start_ms"]
                    chunk["start_ms"] = round(original["start_ms"] + span * left / len(surface))
                    chunk["end_ms"] = round(original["start_ms"] + span * right / len(surface))
                for start, end, _, reading in annotations:
                    absolute_left, absolute_right = unit_start + left, unit_start + right
                    if absolute_left >= start and absolute_right <= end:
                        if original.get("locked") and selection.overwrite_policy != "all":
                            skipped_locked += 1
                        else:
                            chunk["ruby"] = reading
                            chunk["ruby_source"] = "ai"
                            chunk["ruby_confidence"] = 0.9
                            applied += 1
                        break
                next_units.append(chunk)
        line["units"] = next_units
    updated.setdefault("pronunciation", {})["last_run"] = {"mode": "ai", "applied": applied}
    return updated, {"applied": applied, "skipped_locked": skipped_locked, "source": "ai", "text_preserved": True}
