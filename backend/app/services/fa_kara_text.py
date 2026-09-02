from __future__ import annotations

import re
from typing import Any

from pykakasi import kakasi

try:
    from janome.tokenizer import Tokenizer as _JanomeTokenizer
except ImportError:  # Optional until the analysis extras are installed.
    _JanomeTokenizer = None

try:
    import pyphen as _pyphen
except ImportError:  # Optional until the analysis extras are installed.
    _pyphen = None

try:
    import cmudict as _cmudict
except ImportError:  # Optional until the analysis extras are installed.
    _cmudict = None

try:
    from pypinyin import Style as _PinyinStyle, pinyin as _pinyin
except ImportError:  # Optional until the analysis extras are installed.
    _PinyinStyle = None
    _pinyin = None


_KAKASI = kakasi()
_SMALL_KANA = frozenset("ゃゅょぁぃぅぇぉゎゕゖャュョァィゥェォヮヵヶ")
_KANJI_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff々]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z']+")


def normalize_language(value: object) -> str:
    """Keep existing projects Japanese unless they explicitly select Chinese."""
    return "cn" if str(value or "jp").lower() == "cn" else "jp"


def is_kanji(char: str) -> bool:
    return bool(_KANJI_RE.fullmatch(char))


def contains_kanji(text: str) -> bool:
    return bool(_KANJI_RE.search(text))


def missing_japanese_ruby(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return Japanese-kanji units not covered by an existing Ruby group."""
    missing: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        units = line.get("units", [])
        index = 0
        while index < len(units):
            unit = units[index]
            surface = str(unit.get("surface") or "")
            ruby = str(unit.get("ruby") or "").strip()
            if ruby:
                try:
                    span = max(1, int(unit.get("ruby_span") or 1))
                except (TypeError, ValueError):
                    span = 1
                covered = len(surface)
                member_end = index + 1
                while covered < span and member_end < len(units):
                    covered += len(str(units[member_end].get("surface") or ""))
                    member_end += 1
                index = member_end
                continue
            kanji = "".join(char for char in surface if is_kanji(char))
            if kanji:
                missing.append({
                    "line_index": line_index,
                    "line_id": line.get("id"),
                    "unit_id": unit.get("id"),
                    "characters": kanji,
                })
            index += 1
    return missing


def is_kana(char: str) -> bool:
    return "\u3040" <= char <= "\u30ff"


def sylla_split(kana_str: str, *, sokuon_split: bool = False, hatsuon_split: bool = True) -> list[str]:
    """FA-Kara's deterministic Japanese mora splitting rule."""
    result: list[str] = []
    for char in kana_str:
        attach = char in _SMALL_KANA or char == "ー"
        if not sokuon_split:
            attach = attach or char in "っッ"
        if not hatsuon_split:
            attach = attach or char in "んン"
        if attach and result:
            result[-1] += char
        else:
            result.append(char)
    return result


def _english_chunks(word: str) -> list[str]:
    if not word:
        return []
    if _pyphen is None:
        return [word]
    try:
        hyphenated = _pyphen.Pyphen(lang="en_US").inserted(word)
    except (LookupError, OSError, RuntimeError):
        return [word]
    return [part for part in hyphenated.split("-") if part] or [word]


def _cmu_syllables(word: str) -> list[str]:
    if _cmudict is None:
        return []
    try:
        entries = _cmudict.dict().get(word.lower(), [])
    except (LookupError, OSError, RuntimeError):
        return []
    if not entries:
        return []
    vowels = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
    mapping = {
        "AA": "a", "AE": "a", "AH": "a", "AO": "o", "AW": "au", "AY": "ai", "B": "b", "CH": "ch",
        "D": "d", "DH": "z", "EH": "e", "ER": "a", "EY": "ei", "F": "f", "G": "g", "HH": "h",
        "IH": "i", "IY": "i", "JH": "j", "K": "k", "L": "r", "M": "m", "N": "n", "NG": "ng",
        "OW": "o", "OY": "oi", "P": "p", "R": "r", "S": "s", "SH": "sh", "T": "t", "TH": "s",
        "UH": "u", "UW": "u", "V": "v", "W": "w", "Y": "y", "Z": "z", "ZH": "j",
    }
    phonemes = entries[0]
    vowel_positions = [index for index, item in enumerate(phonemes) if item.rstrip("012") in vowels]
    if not vowel_positions:
        return ["".join(mapping.get(item.rstrip("012"), "") for item in phonemes)]
    syllables: list[list[str]] = []
    previous_vowel = -1
    for vowel_index in vowel_positions:
        if previous_vowel < 0:
            syllables.append(phonemes[: vowel_index + 1])
        else:
            consonants = phonemes[previous_vowel + 1 : vowel_index]
            if len(consonants) > 1:
                syllables[-1].append(consonants[0])
                consonants = consonants[1:]
            syllables.append(consonants + [phonemes[vowel_index]])
        previous_vowel = vowel_index
    syllables[-1].extend(phonemes[previous_vowel + 1 :])
    return ["".join(mapping.get(item.rstrip("012"), "") for item in syllable) for syllable in syllables]


def english_phonetic(word: str) -> str:
    return "".join(_cmu_syllables(word)) or "".join(_english_chunks(word)).lower()


def _balanced_groups(values: list[str], group_count: int) -> list[str]:
    if group_count <= 0:
        return []
    result: list[str] = []
    cursor = 0
    for index in range(group_count):
        size = len(values) // group_count + (1 if index >= group_count - len(values) % group_count else 0)
        result.append("".join(values[cursor : cursor + size]))
        cursor += size
    return result


def _english_tokens(word: str) -> list[tuple[str, str]]:
    surfaces = _english_chunks(word)
    readings = _cmu_syllables(word)
    if not readings:
        return [(surface, surface.replace("'", "").lower()) for surface in surfaces]
    if len(surfaces) > len(readings):
        surfaces = _balanced_groups(surfaces, len(readings))
    elif len(readings) > len(surfaces):
        readings = _balanced_groups(readings, len(surfaces))
    return list(zip(surfaces, readings))


def _pinyin_readings(text: str) -> list[str]:
    if _pinyin is None or _PinyinStyle is None:
        return []
    try:
        values = _pinyin(text, style=_PinyinStyle.TONE3)
    except (LookupError, OSError, RuntimeError):
        return []
    return [re.sub(r"[1-5]", "", item[0] if item else "") for item in values]


def _pinyin_phonetic(text: str) -> str:
    return "".join(_pinyin_readings(text))


def phonetic_for_surface(surface: str, *, language: str = "jp") -> str:
    """Produce the Latin token expected by FA-Kara for non-Ruby text."""
    if not surface:
        return ""
    if _LATIN_WORD_RE.fullmatch(surface):
        return english_phonetic(surface)
    if contains_kanji(surface):
        if normalize_language(language) == "cn":
            return _pinyin_phonetic(surface)
        return "".join(str(item.get("hepburn") or item.get("orig") or "") for item in _KAKASI.convert(surface)).lower()
    return "".join(str(item.get("hepburn") or "") for item in _KAKASI.convert(surface)).lower()


def tokenize_fa_kara(text: str, *, language: str = "jp") -> list[tuple[int, int, str]]:
    """Split text with FA-Kara's language-specific surface rules."""
    language = normalize_language(language)
    ranges: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if is_kana(char):
            end = index + 1
            while end < len(text) and is_kana(text[end]):
                end += 1
            cursor = index
            for piece in sylla_split(text[index:end]):
                ranges.append((cursor, cursor + len(piece), phonetic_for_surface(piece, language=language)))
                cursor += len(piece)
            index = end
            continue
        if _LATIN_RE.fullmatch(char):
            end = index + 1
            while end < len(text) and (_LATIN_RE.fullmatch(text[end]) or text[end] == "'"):
                end += 1
            word = text[index:end]
            cursor = index
            for piece, reading in _english_tokens(word):
                ranges.append((cursor, cursor + len(piece), reading))
                cursor += len(piece)
            index = end
            continue
        if char.isdigit():
            end = index + 1
            while end < len(text) and (text[end].isdigit() or text[end] in ".,"):
                end += 1
            ranges.append((index, end, english_phonetic(text[index:end])))
            index = end
            continue
        if language == "cn" and is_kanji(char):
            end = index + 1
            while end < len(text) and is_kanji(text[end]):
                end += 1
            readings = _pinyin_readings(text[index:end])
            for offset, surface_index in enumerate(range(index, end)):
                reading = readings[offset] if offset < len(readings) else ""
                ranges.append((surface_index, surface_index + 1, reading))
            index = end
            continue
        reading = phonetic_for_surface(char, language=language) if is_kanji(char) else ""
        ranges.append((index, index + 1, reading))
        index += 1
    return ranges


def split_fa_kara_ranges(text: str, *, language: str = "jp") -> list[tuple[int, int]]:
    return [(start, end) for start, end, _ in tokenize_fa_kara(text, language=language)]


def _reading_to_hiragana(value: str) -> str:
    return "".join(str(item.get("hira") or item.get("orig") or "") for item in _KAKASI.convert(value))


def local_fa_groups(text: str, *, language: str = "jp") -> list[tuple[int, int, str | None]]:
    """Return FA-Kara/Japanese dictionary groups with optional Ruby readings."""
    if not text:
        return []
    language = normalize_language(language)
    if language == "cn":
        return [(start, end, None) for start, end, _ in tokenize_fa_kara(text, language=language)]
    if _JanomeTokenizer is None:
        return [(start, end, _reading_to_hiragana(text[start:end]) if contains_kanji(text[start:end]) else None) for start, end in split_fa_kara_ranges(text)]
    try:
        tokens = list(_JanomeTokenizer().tokenize(text))
    except (LookupError, OSError, RuntimeError):
        return [(start, end, _reading_to_hiragana(text[start:end]) if contains_kanji(text[start:end]) else None) for start, end in split_fa_kara_ranges(text)]
    groups: list[tuple[int, int, str | None]] = []
    cursor = 0
    for token in tokens:
        surface = str(getattr(token, "surface", ""))
        if not surface:
            continue
        start = text.find(surface, cursor)
        if start < 0:
            continue
        end = start + len(surface)
        cursor = end
        if contains_kanji(surface):
            reading = str(getattr(token, "reading", "") or "")
            if reading and reading != "*":
                groups.append((start, end, _reading_to_hiragana(reading)))
            else:
                groups.append((start, end, _reading_to_hiragana(surface) or None))
        else:
            for left, right in split_fa_kara_ranges(surface):
                groups.append((start + left, start + right, None))
    if cursor < len(text):
        groups.extend((cursor + left, cursor + right, None) for left, right in split_fa_kara_ranges(text[cursor:]))
    return groups


def annotation_segments(text: str, annotations: list[list[Any]]) -> list[tuple[int, int, list[Any] | None]]:
    """Combine protected AI Ruby ranges with deterministic FA-Kara gaps."""
    protected = sorted((int(item[0]), int(item[0]) + len(str(item[1])), item) for item in annotations)
    result: list[tuple[int, int, list[Any] | None]] = []
    cursor = 0
    for start, end, item in protected:
        if start > cursor:
            result.extend((left + cursor, right + cursor, None) for left, right in split_fa_kara_ranges(text[cursor:start]))
        if start < cursor or end > len(text) or text[start:end] != str(item[1]):
            continue
        result.append((start, end, item))
        cursor = end
    if cursor < len(text):
        result.extend((left + cursor, right + cursor, None) for left, right in split_fa_kara_ranges(text[cursor:]))
    return sorted(result, key=lambda value: value[0])
