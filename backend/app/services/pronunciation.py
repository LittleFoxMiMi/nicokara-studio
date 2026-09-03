from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable
import httpx
from uuid import uuid4

from app.services.ai_client import AIClient
from app.services.secrets import SecretStore
from app.services.fa_kara_text import annotation_segments, contains_kanji, is_kana, local_fa_groups, normalize_language, split_fa_kara_ranges, tokenize_fa_kara

DEFAULT_SYSTEM_PROMPT = r'''你是日语卡拉 OK 歌词注音器。你的任务是只为歌词正文中的汉字或包含汉字的特殊表记生成日语假名读音，不得翻译、改写、纠正或删除歌词正文。

你必须只返回一个合法 JSON 对象，不得返回 Markdown 代码围栏、解释、前后缀或注释。固定顶层结构如下：
{
  "result": [
    {
      "line_index": 0,
      "raw": "雨が降った",
      "pronunciation": [
        [0, "雨", "あめ"],
        [2,  "降", "ふ"]
      ]
    }
  ]
}

协议规则：
1. line_index 必须是输入歌词行的 0-based 行号，按输入顺序返回，不得新增、删除、重复或调换行。
2. raw 是歌词原文，不得改变歌词原文。
3. pronunciation 的每项必须严格是 [start, surface, reading]。start是raw里面原始歌词的下标，代表注音汉字的起始位置。
4. reading 只填写实际日语读音，使用平假名；多汉字词作为整体返回，例如 昨日 -> きのう、九十九折 -> つづらおり。
5. 只为汉字或包含汉字的特殊表记返回 pronunciation；假名、助词和标点不要重复返回，surface里面不允许出现汉字以外的内容，例如“降った”只返回“降”和它的注音。没有注音时 pronunciation 返回 []。
6. 不要生成工程内部 id、时间、置信度或其他字段。
7. 连续汉字组成一个完整词、复合词或专有名词时，按整体收录，例如：
   ["着信音", "ちゃくしんおん"]
   ["熊本市", "くまもとし"]
   ["競馬場", "けいばじょう"]
8. 汉字与假名混写时，surface只保留原文中的连续汉字部分，pronunciation只填写该汉字部分在当前词中的读音，不包含送假名。例如：
   押した → ["押", "お"]
   聞こえる → ["聞", "き"]
   集めている → ["集", "あつ"]
   走っている → ["走", "はし"]
9. 一个词中若有多段被假名隔开的汉字，应分别覆盖各段，并按原文顺序输出。例如：
   取り戻す → ["取", "と"], ["戻", "もど"]
10. surface必须与原文实际出现的汉字形式完全一致，不得改写汉字，不得加入送假名。
11. 不收录纯平假名、纯片假名、数字或英文；但其中只要包含汉字，就必须覆盖汉字部分。'''
DEFAULT_USER_TEMPLATE = r'''请严格按照系统协议，为下面的完整歌词生成注音 JSON。歌词正文是唯一真值，Whisper 文本仅用于判断读音，不得据此修改歌词。

歌曲名：{{song_title}}
歌手：{{artist}}

输入歌词行（必须逐行保留 line_index 和 text）：
{{current_lines}}

Whisper 顺序参考（这是语音转文本模型的输出，根据这里的输出来确定多音字的读音，你可能需要进行一定程度的猜测，这里的文本仅供参考，不得用这里的文本代替原有歌词的文本）：
{{whisper_segments}}

再次检查：raw 必须等于每行原文；pronunciation 必须使用 [start,surface,reading]；surface 必须匹配原文；只注音汉字；最后只输出 JSON。返回之前仔细检查json的格式是否有未封闭的括号。'''
READING_RE = re.compile(r"^[ぁ-ゖァ-ヺー・ゔヴーA-Za-z0-9'\- ]+$")


class PronunciationValidationError(ValueError):
    pass


AI_REQUEST_ERRORS = (PronunciationValidationError, ValueError, OSError, TimeoutError, httpx.HTTPError, json.JSONDecodeError)


def resolve_prompt_settings(database: Any, app_settings: dict[str, Any] | None = None) -> tuple[str, str, str | None]:
    """Resolve the active preset, including databases created before preset IDs were stored."""
    values = app_settings if app_settings is not None else database.settings()
    preset_id = values.get("default_prompt_preset_id")
    preset = database.get_prompt_preset(str(preset_id)) if preset_id else None
    if not preset:
        presets = database.list_prompt_presets()
        preset = presets[0] if presets else None
    if preset:
        return str(preset["system_prompt"]), str(preset["user_template"]), str(preset["id"])
    return (
        str(values.get("pronunciation_system_prompt") or DEFAULT_SYSTEM_PROMPT),
        str(values.get("pronunciation_user_template") or DEFAULT_USER_TEMPLATE),
        None,
    )


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
    readings = [reading for _, _, reading in local_fa_groups(surface) if reading]
    reading = "".join(readings)
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
    language = normalize_language(updated.get("project", {}).get("language"))
    applied = 0
    skipped_locked = 0
    low_confidence = 0
    selected_lines = set(selection.line_ids)
    selected_units = set(selection.unit_ids)
    for line in updated.get("lyrics", {}).get("lines", []):
        line_selected = line["id"] in selected_lines
        if (selected_lines or selected_units) and not line_selected and not any(unit["id"] in selected_units for unit in line.get("units", [])):
            continue
        rebuilt: list[dict] = []
        for original in line.get("units", []):
            if selected_units and not line_selected and original.get("id") not in selected_units:
                rebuilt.append(original)
                continue
            if original.get("locked") and selection.overwrite_policy != "all":
                skipped_locked += 1
                rebuilt.append(original)
                continue
            surface = str(original.get("surface", ""))
            groups = local_fa_groups(surface, language=language)
            alignment_readings = {(start, end): reading for start, end, reading in tokenize_fa_kara(surface, language=language)}
            if not groups:
                rebuilt.append(original)
                continue
            for group_index, (group_start, group_end, reading) in enumerate(groups):
                group_surface = surface[group_start:group_end]
                display_reading = reading if reading and any(is_kana(char) for char in reading) else None
                pieces = [(group_start + left, group_start + right) for left, right in split_fa_kara_ranges(group_surface, language=language)] if display_reading and contains_kanji(group_surface) else [(group_start, group_end)]
                for piece_index, (left, right) in enumerate(pieces):
                    unit = deepcopy(original)
                    unit["surface"] = surface[left:right]
                    if group_index or piece_index:
                        unit["id"] = str(uuid4())
                    unit["ruby"] = None
                    unit["ruby_source"] = "none"
                    unit.pop("ruby_confidence", None)
                    unit["ruby_span"] = 0
                    token_reading = alignment_readings.get((left, right))
                    if token_reading and not display_reading:
                        unit["alignment_reading"] = token_reading
                    else:
                        unit.pop("alignment_reading", None)
                    if display_reading and piece_index == 0:
                        unit["ruby"] = display_reading
                        unit["ruby_source"] = "local_fallback" if fallback else "local"
                        unit["ruby_confidence"] = 0.72
                        unit["ruby_span"] = len(group_surface)
                        applied += 1
                    if original.get("start_ms") is not None and original.get("end_ms") is not None and surface:
                        span = original["end_ms"] - original["start_ms"]
                        unit["start_ms"] = round(original["start_ms"] + span * left / len(surface))
                        unit["end_ms"] = round(original["start_ms"] + span * right / len(surface))
                    rebuilt.append(unit)
        line["units"] = rebuilt
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
        if check_raw and (not isinstance(raw, str) or raw != text):
            raise PronunciationValidationError(f"第 {index + 1} 行 raw 必须与歌词正文完全一致")
        ranges = item.get("pronunciation", [])
        if not isinstance(ranges, list):
            raise PronunciationValidationError("pronunciation 必须是数组")
        last_end = -1
        checked = []
        for entry in ranges:
            if not isinstance(entry, list) or len(entry) != 3:
                raise PronunciationValidationError("注音项必须是 [start,surface,reading]")
            start, surface, reading = entry
            end = start + len(surface) if isinstance(start, int) and isinstance(surface, str) else -1
            if not isinstance(start, int) or not isinstance(surface, str) or start < 0 or end <= start or end > len(text) or start < last_end:
                raise PronunciationValidationError("注音位置越界或重叠")
            if text[start:end] != surface or not contains_kanji(surface) or not isinstance(reading, str) or not READING_RE.match(reading):
                raise PronunciationValidationError("注音 surface 或 reading 不合法")
            checked.append([start, surface, reading])
            last_end = end
        validated.append({"line_index": index, "raw": text, "pronunciation": checked})
        seen.add(index)
    return validated


def validate_ai_result_partial(payload: Any, lines: list[dict], allowed_line_indices: set[int] | None = None) -> tuple[list[dict], set[int], set[int]]:
    """Keep valid rows while rejecting malformed full-line protocol rows."""
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
            if not isinstance(item.get("raw"), str) or item.get("raw") != "".join(
                unit.get("surface", "") for unit in line_map[index].get("units", [])
            ):
                raw_mismatch.add(index)
            invalid.add(index)
    expected = allowed_line_indices if allowed_line_indices is not None else set(line_map)
    invalid.update(expected - seen)
    return valid, invalid, raw_mismatch


def apply_ai(document: dict, result: list[dict], selection: PronunciationSelection) -> tuple[dict, dict]:
    updated = deepcopy(document)
    language = normalize_language(updated.get("project", {}).get("language"))
    if language == "cn":
        raise PronunciationValidationError("中文工程不需要注音")
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
        line_text = "".join(str(unit.get("surface", "")) for unit in line.get("units", []))
        segments = annotation_segments(line_text, annotations)
        alignment_readings = {(start, end): reading for start, end, reading in tokenize_fa_kara(line_text, language=language)}
        # AI is a Japanese-kanji Ruby provider; Chinese/English phonetics stay
        # in the deterministic FA-Kara alignment field and are never surfaced.
        japanese_context = language == "jp"
        cursor = 0
        next_units: list[dict] = []
        for original in line.get("units", []):
            surface = str(original.get("surface", ""))
            unit_start, unit_end = cursor, cursor + len(surface)
            cursor = unit_end
            if selected_units and not line_selected and original.get("id") not in selected_units:
                next_units.append(deepcopy(original))
                continue
            if original.get("locked") and selection.overwrite_policy != "all":
                skipped_locked += 1
                next_units.append(deepcopy(original))
                continue
            local_segments: list[tuple[int, int, list[Any] | None]] = []
            for start, end, annotation in segments:
                left, right = max(start, unit_start), min(end, unit_end)
                if left < right:
                    local_segments.append((left, right, annotation))
            if not local_segments and surface:
                local_segments = [(unit_start + left, unit_start + right, None) for left, right in split_fa_kara_ranges(surface, language=language)]
            expanded_segments: list[tuple[int, int, list[Any] | None]] = []
            for absolute_left, absolute_right, annotation in local_segments:
                if annotation is None:
                    expanded_segments.append((absolute_left, absolute_right, None))
                    continue
                local_left = absolute_left - unit_start
                local_right = absolute_right - unit_start
                for left, right in split_fa_kara_ranges(surface[local_left:local_right], language=language):
                    expanded_segments.append((absolute_left + left, absolute_left + right, annotation))
            for chunk_index, (absolute_left, absolute_right, annotation) in enumerate(expanded_segments):
                left, right = absolute_left - unit_start, absolute_right - unit_start
                chunk = deepcopy(original)
                chunk["surface"] = surface[left:right]
                if chunk_index:
                    chunk["id"] = str(uuid4())
                chunk["ruby"] = None
                chunk["ruby_source"] = "none"
                chunk.pop("ruby_confidence", None)
                chunk["ruby_span"] = 0
                token_reading = alignment_readings.get((absolute_left, absolute_right))
                if token_reading and not (japanese_context and contains_kanji(chunk["surface"])):
                    chunk["alignment_reading"] = token_reading
                else:
                    chunk.pop("alignment_reading", None)
                if annotation is not None:
                    _, _, reading = annotation
                if original.get("start_ms") is not None and original.get("end_ms") is not None and surface:
                    span = original["end_ms"] - original["start_ms"]
                    chunk["start_ms"] = round(original["start_ms"] + span * left / len(surface))
                    chunk["end_ms"] = round(original["start_ms"] + span * right / len(surface))
                if annotation is not None:
                    annotation_start = int(annotation[0])
                    annotation_end = annotation_start + len(str(annotation[1]))
                    if absolute_left == annotation_start:
                        chunk["ruby"] = reading
                        chunk["ruby_source"] = "ai"
                        chunk["ruby_confidence"] = 0.9
                        chunk["ruby_span"] = annotation_end - annotation_start
                        applied += 1
                next_units.append(chunk)
        line["units"] = next_units
    updated.setdefault("pronunciation", {})["last_run"] = {"mode": "ai", "applied": applied}
    return updated, {"applied": applied, "skipped_locked": skipped_locked, "source": "ai", "text_preserved": True}


def _complete_with_retry(
    client: AIClient,
    system_prompt: str,
    user_prompt: str,
    retry_count: int,
    validate: Callable[[dict], tuple[list[dict], set[int], set[int]]],
) -> tuple[dict, list[dict], set[int], set[int], int]:
    retries = max(0, min(10, int(retry_count)))
    for attempt in range(retries + 1):
        try:
            raw = client.complete(system_prompt, user_prompt)
            valid, invalid, raw_mismatch = validate(raw)
            if (invalid or raw_mismatch) and attempt < retries:
                raise PronunciationValidationError(f"{len(invalid)} 行校验失败")
            return raw, valid, invalid, raw_mismatch, attempt
        except AI_REQUEST_ERRORS:
            if attempt >= retries:
                raise
    raise RuntimeError("unreachable")


def run_ai_pronunciation(
    *,
    database: Any,
    settings: Any,
    project_id: str,
    document: dict,
    selection: PronunciationSelection,
    profile_id: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> tuple[dict, dict]:
    """Run all AI batches and commit one revision, optionally reporting batch progress."""
    profile_id = profile_id or database.settings().get("default_ai_profile_id")
    profile = database.get_ai_profile(profile_id) if profile_id else None
    app_settings = database.settings()
    if not profile:
        raise PronunciationValidationError("尚未配置 AI 注音 profile")
    key = SecretStore(settings.data_dir).decrypt(database.get_ai_profile_secret(profile["id"]))
    if not key:
        raise PronunciationValidationError("AI profile 未配置密钥")
    lines = document.get("lyrics", {}).get("lines", [])
    current_lines = [{"line_index": index, "text": "".join(unit.get("surface", "") for unit in line.get("units", []))} for index, line in enumerate(lines)]
    transcript_path = settings.projects_dir / project_id / "derived" / "transcript.json"
    if not transcript_path.is_file():
        raise PronunciationValidationError("AI 注音需要先完成 Whisper 粗识别")
    try:
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        segments = transcript.get("segments") if isinstance(transcript, dict) else None
        if not isinstance(segments, list) or any(not isinstance(item, dict) for item in segments):
            raise ValueError("invalid_segments")
        whisper_segments = [
            {"segment_index": index, "text": str(item.get("text", ""))}
            for index, item in enumerate(segments)
        ]
    except (OSError, ValueError) as exc:
        raise PronunciationValidationError("Whisper 粗识别结果无效，请重新运行粗识别") from exc
    system_prompt, user_template, _ = resolve_prompt_settings(database, app_settings)
    line_batches = chunk_lines(current_lines, profile.get("max_chars_per_request", 1200))
    proxy = str(app_settings.get("proxy_url") or "") if app_settings.get("proxy_enabled", True) else None
    try:
        client = AIClient(profile, key, proxy)
        raw_batches: list[dict] = []
        result: list[dict] = []
        invalid_line_indices: set[int] = set()
        raw_mismatch_line_indices: set[int] = set()
        retries_used = 0
        total = max(1, len(line_batches))
        for batch_index, batch in enumerate(line_batches):
            if progress_callback:
                progress_callback(batch_index / total, f"AI 注音：正在提交第 {batch_index + 1}/{total} 批")
            batch_prompt = render_prompt(
                user_template,
                song_title=document.get("project", {}).get("title", ""),
                artist=document.get("project", {}).get("artist", ""),
                current_lines=batch,
                whisper_segments=whisper_segments,
            )
            allowed_indices = {item["line_index"] for item in batch}
            raw, validated, invalid_indices, raw_mismatch_indices, used = _complete_with_retry(
                client,
                system_prompt,
                batch_prompt,
                profile.get("retry_count", 2),
                lambda value: validate_ai_result_partial(value, lines, allowed_indices),
            )
            raw_batches.append(raw)
            retries_used += used
            result.extend(validated)
            invalid_line_indices.update(invalid_indices)
            raw_mismatch_line_indices.update(raw_mismatch_indices)
            if progress_callback:
                progress_callback((batch_index + 1) / total, f"AI 注音：第 {batch_index + 1}/{total} 批完成")
        selected_line_ids = set(selection.line_ids)
        selected_unit_ids = set(selection.unit_ids)
        failed_line_ids = [
            lines[index]["id"]
            for index in sorted(invalid_line_indices)
            if not (selected_line_ids or selected_unit_ids)
            or lines[index]["id"] in selected_line_ids
            or any(unit["id"] in selected_unit_ids for unit in lines[index].get("units", []))
        ]
        if failed_line_ids:
            raise PronunciationValidationError(
                f"AI 注音有 {len(failed_line_ids)} 行校验失败，未写入任何结果"
            )
        updated, summary = apply_ai(document, result, selection)
        updated.setdefault("pronunciation", {})["last_run"] = {"mode": "ai", "applied": summary["applied"]}
        if progress_callback:
            progress_callback(1.0, "AI 注音完成")
        return updated, {**summary, "batch_count": len(line_batches), "retry_count": retries_used, "raw_mismatch_lines": len(raw_mismatch_line_indices)}
    except AI_REQUEST_ERRORS as exc:
        if isinstance(exc, PronunciationValidationError):
            raise
        raise PronunciationValidationError(f"AI 注音请求失败：{str(exc)[:200]}") from exc
