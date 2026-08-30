from __future__ import annotations

import json
from typing import Callable
import httpx
from fastapi import APIRouter, HTTPException, Request

from app.core.database import Database
from app.schemas.projects import PronunciationRequest
from app.services.pronunciation import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_TEMPLATE,
    PronunciationSelection,
    PronunciationValidationError,
    apply_ai,
    apply_local,
    chunk_lines,
    render_prompt,
    validate_ai_result_partial,
)
from app.services.secrets import SecretStore
from app.services.ai_client import AIClient

router = APIRouter(prefix="/projects", tags=["pronunciation"])

AI_REQUEST_ERRORS = (PronunciationValidationError, ValueError, OSError, TimeoutError, httpx.HTTPError, json.JSONDecodeError)


def _complete_with_retry(client: AIClient, system_prompt: str, user_prompt: str, retry_count: int, validate: Callable[[dict], tuple[list[dict], set[int], set[int]]]) -> tuple[dict, list[dict], set[int], set[int], int]:
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


def _project(request: Request, project_id: str) -> tuple[Database, dict, dict]:
    db: Database = request.app.state.database
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    document = db.document(project_id)
    if not document:
        raise HTTPException(404, "工程文档不存在")
    return db, project, document


def _selection(document: dict, payload: PronunciationRequest) -> PronunciationSelection:
    lines = document.get("lyrics", {}).get("lines", [])
    line_ids = {line["id"] for line in lines}
    unit_ids = {unit["id"] for line in lines for unit in line.get("units", [])}
    if not set(payload.line_ids).issubset(line_ids) or not set(payload.unit_ids).issubset(unit_ids):
        raise HTTPException(422, "注音范围包含未知歌词 ID")
    return PronunciationSelection(payload.line_ids, payload.unit_ids, payload.overwrite_policy)


@router.post("/{project_id}/pronunciation/local")
def local_pronunciation(project_id: str, payload: PronunciationRequest, request: Request):
    db, project, document = _project(request, project_id)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    selection = _selection(document, payload)
    updated, summary = apply_local(document, selection)
    try:
        saved = db.save_document(project_id, updated, payload.revision)
    except ValueError as exc:
        raise HTTPException(409, {"code": "revision_conflict"}) from exc
    return {"revision": saved["revision"], "document": updated, "summary": summary}


@router.post("/{project_id}/pronunciation/ai")
def ai_pronunciation(project_id: str, payload: PronunciationRequest, request: Request):
    db, project, document = _project(request, project_id)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    selection = _selection(document, payload)
    profile_id = payload.profile_id or db.settings().get("default_ai_profile_id")
    profile = db.get_ai_profile(profile_id) if profile_id else None
    settings = db.settings()
    fallback = str(settings.get("ai_failure_mode", "auto_local")) == "auto_local"
    if not profile:
        if fallback:
            updated, summary = apply_local(document, selection, fallback=True)
            saved = db.save_document(project_id, updated, payload.revision)
            return {"revision": saved["revision"], "document": updated, "summary": {**summary, "fallback_reason": "未配置 AI profile"}}
        raise HTTPException(422, "尚未配置 AI 注音 profile")
    key = SecretStore(request.app.state.settings.data_dir).decrypt(db.get_ai_profile_secret(profile["id"]))
    if not key:
        if fallback:
            updated, summary = apply_local(document, selection, fallback=True)
            saved = db.save_document(project_id, updated, payload.revision)
            return {"revision": saved["revision"], "document": updated, "summary": {**summary, "fallback_reason": "AI profile 未配置密钥"}}
        raise HTTPException(422, "AI profile 未配置密钥")
    lines = document.get("lyrics", {}).get("lines", [])
    current_lines = [{"line_index": index, "text": "".join(unit.get("surface", "") for unit in line.get("units", []))} for index, line in enumerate(lines)]
    whisper_segments: list[dict] = []
    transcript = request.app.state.settings.projects_dir / project_id / "derived" / "transcript.json"
    if transcript.is_file():
        try:
            whisper_segments = [{"segment_index": index, "text": item.get("text", "")} for index, item in enumerate(json.loads(transcript.read_text(encoding="utf-8")).get("segments", []))]
        except (OSError, ValueError):
            whisper_segments = []
    prompt_settings = settings
    system_prompt = str(prompt_settings.get("pronunciation_system_prompt") or DEFAULT_SYSTEM_PROMPT)
    user_template = str(prompt_settings.get("pronunciation_user_template") or DEFAULT_USER_TEMPLATE)
    line_batches = chunk_lines(current_lines, profile.get("max_chars_per_request", 1200))
    proxy = str(settings.get("proxy_url") or "") if settings.get("proxy_enabled", True) else None
    try:
        client = AIClient(profile, key, proxy)
        batch_prompts: list[str] = []
        raw_batches: list[dict] = []
        result: list[dict] = []
        invalid_line_indices: set[int] = set()
        raw_mismatch_line_indices: set[int] = set()
        retries_used = 0
        for batch_index, batch in enumerate(line_batches):
            batch_prompt = render_prompt(user_template, song_title=document.get("project", {}).get("title", ""), artist=document.get("project", {}).get("artist", ""), current_lines=batch, whisper_segments=whisper_segments)
            batch_prompts.append(batch_prompt)
            allowed_indices = {item["line_index"] for item in batch}
            raw, validated, invalid_indices, raw_mismatch_indices, used = _complete_with_retry(client, system_prompt, batch_prompt, profile.get("retry_count", 2), lambda value: validate_ai_result_partial(value, lines, allowed_indices))
            raw_batches.append(raw)
            retries_used += used
            result.extend(validated)
            invalid_line_indices.update(invalid_indices)
            raw_mismatch_line_indices.update(raw_mismatch_indices)
        selected_line_ids = set(selection.line_ids)
        selected_unit_ids = set(selection.unit_ids)
        fallback_line_ids = [
            lines[index]["id"]
            for index in sorted(invalid_line_indices)
            if not (selected_line_ids or selected_unit_ids)
            or lines[index]["id"] in selected_line_ids
            or any(unit["id"] in selected_unit_ids for unit in lines[index].get("units", []))
        ]
        if fallback_line_ids:
            updated, local_summary = apply_local(document, PronunciationSelection(fallback_line_ids, [], selection.overwrite_policy), fallback=True)
        else:
            updated, local_summary = document, {"applied": 0}
        updated, summary = apply_ai(updated, result, selection)
        saved = db.save_document(project_id, updated, payload.revision)
        combined_raw = raw_batches[0] if len(raw_batches) == 1 else {"result": [item for raw in raw_batches for item in raw.get("result", [])]}
        return {"revision": saved["revision"], "document": updated, "summary": {**summary, "batch_count": len(line_batches), "retry_count": retries_used, "local_fallback_lines": len(fallback_line_ids), "local_fallback_applied": local_summary["applied"], "raw_mismatch_lines": len(raw_mismatch_line_indices)}, "prompt": {"system": system_prompt, "user": batch_prompts[0] if len(line_batches) == 1 else batch_prompts}, "raw_result": combined_raw}
    except AI_REQUEST_ERRORS as exc:
        if fallback:
            updated, summary = apply_local(document, selection, fallback=True)
            saved = db.save_document(project_id, updated, payload.revision)
            return {"revision": saved["revision"], "document": updated, "summary": {**summary, "fallback_reason": str(exc)[:200]}}
        raise HTTPException(502, "AI 注音失败，未覆盖现有注音") from exc
