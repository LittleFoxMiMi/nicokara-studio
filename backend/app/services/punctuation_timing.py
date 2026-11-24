from __future__ import annotations

from unicodedata import category


def is_silent_punctuation(char: str, previous: str = "", following: str = "") -> bool:
    if char in {"'", "’"} and all(c.isascii() and c.isalpha() for c in (previous, following)):
        return False
    if char in {".", ","} and previous.isdecimal() and following.isdecimal():
        return False
    return category(char).startswith("P") or char in {"~", "～"}


def valid_ruby_timing(unit: dict, start: int, end: int) -> bool:
    parts = unit.get("ruby_timing")
    if not isinstance(parts, list) or not parts or not unit.get("ruby"):
        return False
    cursor = start
    reading = []
    for part in parts:
        if not isinstance(part, dict):
            return False
        text, left, right = part.get("text"), part.get("start_ms"), part.get("end_ms")
        if not isinstance(text, str) or not text or not isinstance(left, int) or not isinstance(right, int):
            return False
        if not cursor <= left <= right <= end:
            return False
        reading.append(text)
        cursor = right
    return "".join(reading) == unit["ruby"]


def punctuation_export_units(line: dict, units: list[dict]) -> list[dict] | None:
    """Assign export windows to text and silent marks, preserving Ruby groups."""
    text = "".join(unit["surface"] for unit in units)
    silent = [is_silent_punctuation(char, text[i - 1] if i else "", text[i + 1:i + 2]) for i, char in enumerate(text)]
    if not any(silent):
        return None

    silent = [flag or char.isspace() for char, flag in zip(text, silent)]
    ranges = []
    ruby_windows: dict[int, tuple[float, float]] = {}
    line_level = line.get("timing_precision") == "line"
    covered_until = 0
    offset = 0
    for i, unit in enumerate(units):
        end = offset + len(unit["surface"])
        if offset >= covered_until and (unit.get("ruby") or unit.get("ruby_2")):
            span = max(end - offset, int(unit.get("ruby_span") or 1))
            if not unit.get("ruby_span"):
                for following in units[i + 1:]:
                    if (following.get("ruby"), following.get("ruby_2")) != (unit.get("ruby"), unit.get("ruby_2")):
                        break
                    span += len(following["surface"])
            covered_until = min(len(text), offset + span)
            silent[offset:covered_until] = [False] * (covered_until - offset)
            timing = unit.get("ruby_timing")
            if timing and not line_level:
                members = []
                chars = 0
                for member in units[i:]:
                    chars += len(member["surface"])
                    members.append(member)
                    if chars >= span:
                        break
                starts = [member["start_ms"] for member in members if member.get("start_ms") is not None]
                ends = [member["end_ms"] for member in members if member.get("end_ms") is not None]
                if starts and ends and valid_ruby_timing(unit, min(starts), max(ends)):
                    start_ms, end_ms = timing[0]["start_ms"], timing[-1]["end_ms"]
                    consumed = 0
                    for member_index, member in enumerate(members, i):
                        left = start_ms + (end_ms - start_ms) * consumed / chars
                        consumed += len(member["surface"])
                        right = start_ms + (end_ms - start_ms) * consumed / chars
                        ruby_windows[member_index] = (round(left), round(right))
        ranges.append((offset, end))
        offset = end
    if not any(silent):
        return None

    line_start = line.get("start_ms") or 0
    line_end = line.get("end_ms") if line.get("end_ms") is not None else line_start + 500
    spoken_count = max(1, silent.count(False))
    spoken_offset = 0
    parts: list[dict] = []
    for index, (unit, (offset, end)) in enumerate(zip(units, ranges)):
        flags = silent[offset:end]
        count = flags.count(False)
        start_ms, end_ms = unit.get("start_ms"), unit.get("end_ms")
        if index in ruby_windows:
            start_ms, end_ms = ruby_windows[index]
        elif line_level or start_ms is None or end_ms is None:
            start_ms = line_start + (line_end - line_start) * spoken_offset / spoken_count
            end_ms = line_start + (line_end - line_start) * (spoken_offset + count) / spoken_count
        spoken_offset += count
        if count == len(flags):
            parts.append({**unit, "start_ms": start_ms, "end_ms": end_ms})
            continue
        spoken = 0
        for char, is_silent in zip(unit["surface"], flags):
            left = start_ms + (end_ms - start_ms) * spoken / max(1, count)
            spoken += not is_silent
            right = start_ms + (end_ms - start_ms) * spoken / max(1, count)
            parts.append({**unit, "surface": char, "start_ms": left, "end_ms": right, "_silent_punctuation": is_silent})

    # In the middle, split the available time evenly across the marks. At the
    # ends (or when adjacent lyrics overlap), the available duration is zero.
    index = 0
    while index < len(parts):
        if not parts[index].get("_silent_punctuation"):
            index += 1
            continue
        end = index + 1
        while end < len(parts) and parts[end].get("_silent_punctuation"):
            end += 1
        previous = parts[index - 1]["end_ms"] if index else None
        following = parts[end]["start_ms"] if end < len(parts) else None
        start_ms = previous if previous is not None else following if following is not None else line_start
        end_ms = max(start_ms, following if following is not None else start_ms)
        width = max(1, sum(not part["surface"].isspace() for part in parts[index:end]))
        cursor = 0
        for i in range(index, end):
            parts[i]["start_ms"] = start_ms + (end_ms - start_ms) * cursor / width
            cursor += not parts[i]["surface"].isspace()
            parts[i]["end_ms"] = start_ms + (end_ms - start_ms) * cursor / width
        index = end
    return parts
