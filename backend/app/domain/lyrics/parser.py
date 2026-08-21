from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

LyricsFormat = Literal["text", "lrc", "krl"]

TIME_SOURCE = r"\[(\d{1,3}):([0-5]?\d)(?:[\.:](\d{1,3}))?\]"
TIME_RE = re.compile(TIME_SOURCE)
LEADING_TIMES_RE = re.compile(rf"^(?:{TIME_SOURCE})+")
METADATA_RE = re.compile(r"^\[(ar|al|ti|by|offset|re|ve|length):", re.IGNORECASE)
ROLE_RE = re.compile(r"【@([^】]+)】")
INLINE_RUBY_RE = re.compile(r"\{([^|{}]+)\|([^{}]+)\}")
RUBY_DEFINITION_RE = re.compile(r"^@ruby\d*\s*=\s*([^,]+)\s*,\s*([^,]+)", re.IGNORECASE)
TOKEN_RE = re.compile(rf"({TIME_SOURCE})|(【@[^】]+】)|(\{{[^|{{}}]+\|[^{{}}]+\}})|([\s\S])")


@dataclass(frozen=True)
class Detection:
    format: LyricsFormat
    confidence: float
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"format": self.format, "confidence": self.confidence, "reasons": self.reasons}


def _clean(content: str) -> str:
    return content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def parse_time_ms(tag: str) -> int:
    match = TIME_RE.fullmatch(tag)
    if not match:
        raise ValueError(f"无效时间标签: {tag}")
    minutes, seconds, fraction = match.groups()
    fraction_ms = 0
    if fraction:
        fraction_ms = int(fraction) * (100 if len(fraction) == 1 else 10 if len(fraction) == 2 else 1)
    return (int(minutes) * 60 + int(seconds)) * 1000 + fraction_ms


def detect_lyrics_format(content: str, filename: str | None = None) -> Detection:
    text = _clean(content)
    suffix = (filename or "").lower().rsplit(".", 1)[-1]
    reasons: list[str] = []
    has_time = False
    enhanced = suffix == "krl"
    if enhanced:
        reasons.append("文件扩展名为 .krl")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if RUBY_DEFINITION_RE.match(line):
            enhanced = True
            reasons.append("包含 @Ruby 定义")
        if ROLE_RE.search(line) or INLINE_RUBY_RE.search(line):
            enhanced = True
            reasons.append("包含 Kirakara 角色或行内 Ruby 标签")
        matches = list(TIME_RE.finditer(line))
        if matches:
            has_time = True
            leading = LEADING_TIMES_RE.match(line)
            body_start = leading.end() if leading else 0
            if any(match.start() >= body_start for match in matches):
                enhanced = True
                reasons.append("歌词正文中包含逐字时间标签")

    if enhanced and has_time:
        return Detection("krl", 0.99, list(dict.fromkeys(reasons)))
    if has_time:
        return Detection("lrc", 0.98, ["包含行级 LRC 时间标签"])
    return Detection("text", 0.96, ["未发现时间标签"])


def _unit(surface: str, **values: object) -> dict:
    return {
        "id": str(uuid4()),
        "surface": surface,
        "start_ms": None,
        "end_ms": None,
        "timing_source": "none",
        "timing_confidence": None,
        "ruby": None,
        "ruby_2": None,
        "ruby_source": "none",
        "roles": [],
        "locked": False,
        **values,
    }


def _line(order: int, units: list[dict], **values: object) -> dict:
    return {
        "id": str(uuid4()),
        "order": order,
        "start_ms": None,
        "end_ms": None,
        "anchor_ms": None,
        "timing_source": "none",
        "timing_precision": "none",
        "units": units,
        **values,
    }


def _parse_text(content: str) -> list[dict]:
    lines: list[dict] = []
    for raw in _clean(content).splitlines():
        surface = raw.strip()
        if surface:
            lines.append(_line(len(lines), [_unit(surface)]))
    return lines


def _parse_ruby_definitions(content: str) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for raw in _clean(content).splitlines():
        match = RUBY_DEFINITION_RE.match(raw.strip())
        if match:
            definitions.setdefault(match.group(1).strip(), TIME_RE.sub("", match.group(2)).strip())
    return definitions


def _apply_external_ruby(units: list[dict], definitions: dict[str, str]) -> None:
    if not definitions:
        return
    index = 0
    while index < len(units):
        matched = False
        for surface in sorted(definitions, key=len, reverse=True):
            candidate = "".join(unit["surface"] for unit in units[index : index + len(surface)])
            if candidate != surface or any(unit.get("ruby") for unit in units[index : index + len(surface)]):
                continue
            units[index]["ruby"] = definitions[surface]
            units[index]["ruby_span"] = len(surface)
            units[index]["ruby_source"] = "imported"
            index += len(surface)
            matched = True
            break
        if not matched:
            index += 1


def _plain_units(text: str, roles: list[str] | None = None) -> list[dict]:
    return [_unit(text, roles=list(roles or []))] if text else []


def _tokenize_krl(body: str, initial_ms: int) -> tuple[list[dict], bool]:
    tokens: list[dict] = [{"type": "time", "time_ms": initial_ms}]
    roles: list[str] = []
    ends_with_time = False
    for match in TOKEN_RE.finditer(body):
        value = match.group(0)
        if TIME_RE.fullmatch(value):
            tokens.append({"type": "time", "time_ms": parse_time_ms(value)})
            ends_with_time = True
            continue
        ends_with_time = False
        role_match = ROLE_RE.fullmatch(value)
        if role_match:
            roles = [part.strip() for part in role_match.group(1).split("+") if part.strip()]
            continue
        ruby_match = INLINE_RUBY_RE.fullmatch(value)
        if ruby_match:
            surface, readings = ruby_match.groups()
            ruby, separator, ruby_2 = readings.partition(">")
            ruby = TIME_RE.sub("", ruby).strip()
            ruby_2 = TIME_RE.sub("", ruby_2).strip() if separator else ""
            for char_index, char in enumerate(surface):
                values: dict[str, object] = {"roles": list(roles)}
                if char_index == 0:
                    values.update(
                        ruby=ruby or None,
                        ruby_2=ruby_2 or None,
                        ruby_span=len(surface),
                        ruby_source="imported",
                    )
                tokens.append({"type": "unit", "unit": _unit(char, **values)})
            continue
        tokens.append({"type": "unit", "unit": _unit(value, roles=list(roles))})
    return tokens, ends_with_time


def _timed_units(tokens: list[dict], fallback_end_ms: int) -> tuple[list[dict], bool]:
    segments: list[tuple[int, int | None, list[dict]]] = []
    cursor: int | None = None
    pending: list[dict] = []
    for token in tokens:
        if token["type"] == "time":
            next_time = int(token["time_ms"])
            if pending:
                segments.append((cursor if cursor is not None else max(0, next_time - 150), next_time, pending))
            cursor, pending = next_time, []
        else:
            pending.append(token["unit"])
    estimated_tail = bool(pending)
    if pending:
        segments.append((cursor or 0, fallback_end_ms, pending))

    result: list[dict] = []
    for start_ms, end_ms, units in segments:
        safe_end = max(start_ms + len(units) * 20, end_ms or fallback_end_ms)
        span = safe_end - start_ms
        for index, unit in enumerate(units):
            unit["start_ms"] = round(start_ms + span * index / len(units))
            unit["end_ms"] = round(start_ms + span * (index + 1) / len(units))
            unit["timing_source"] = "estimated" if end_ms is None else "imported"
            unit["timing_confidence"] = None if end_ms is None else 1.0
            result.append(unit)
    return result, estimated_tail


def _source_lines(content: str) -> list[tuple[int, str]]:
    source: list[tuple[int, str]] = []
    for raw in _clean(content).splitlines():
        line = raw.strip()
        if not line or METADATA_RE.match(line) or RUBY_DEFINITION_RE.match(line):
            continue
        leading = LEADING_TIMES_RE.match(line)
        if not leading:
            continue
        tags = [match.group(0) for match in TIME_RE.finditer(leading.group(0))]
        body = line[leading.end() :]
        for tag in tags:
            source.append((parse_time_ms(tag), body))
    source.sort(key=lambda item: item[0])
    return source


def _line_end(source: list[tuple[int, str]], index: int, media_duration_ms: int | None) -> int:
    start = source[index][0]
    for next_start, _ in source[index + 1 :]:
        if next_start > start:
            return next_start
    if media_duration_ms and media_duration_ms > start:
        return media_duration_ms
    return start + 5000


def _parse_lrc(content: str, enhanced: bool, media_duration_ms: int | None) -> list[dict]:
    source = _source_lines(content)
    definitions = _parse_ruby_definitions(content)
    lines: list[dict] = []
    for index, (start_ms, body) in enumerate(source):
        fallback_end = _line_end(source, index, media_duration_ms)
        if enhanced:
            tokens, ends_with_time = _tokenize_krl(body, start_ms)
            units, estimated_tail = _timed_units(tokens, fallback_end)
            precision = "unit" if any(TIME_RE.finditer(body)) else "line"
            if units and (estimated_tail or not ends_with_time):
                units[-1]["timing_source"] = "estimated"
                units[-1]["timing_confidence"] = None
        else:
            clean_body = ROLE_RE.sub("", body)
            units = _plain_units(clean_body)
            span = max(len(units) * 20, fallback_end - start_ms)
            for unit_index, unit in enumerate(units):
                unit["start_ms"] = round(start_ms + span * unit_index / max(1, len(units)))
                unit["end_ms"] = round(start_ms + span * (unit_index + 1) / max(1, len(units)))
                unit["timing_source"] = "estimated"
            precision = "line"
        if not units:
            continue
        _apply_external_ruby(units, definitions)
        lines.append(
            _line(
                len(lines),
                units,
                start_ms=units[0]["start_ms"],
                end_ms=units[-1]["end_ms"],
                anchor_ms=start_ms,
                timing_source="imported",
                timing_precision=precision,
            )
        )
    return lines


def parse_lyrics(
    content: str,
    requested_format: LyricsFormat | Literal["auto"] = "auto",
    *,
    filename: str | None = None,
    media_duration_ms: int | None = None,
) -> dict:
    detection = detect_lyrics_format(content, filename)
    selected: LyricsFormat = detection.format if requested_format == "auto" else requested_format
    if selected == "text":
        lines = _parse_text(content)
    else:
        lines = _parse_lrc(content, selected == "krl", media_duration_ms)
    if not lines:
        raise ValueError("没有找到可导入的歌词行")
    return {
        "source_type": selected,
        "detected_type": detection.format,
        "original_filename": filename,
        "lines": lines,
    }
