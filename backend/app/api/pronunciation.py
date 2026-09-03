from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.database import Database
from app.schemas.projects import PronunciationRequest
from app.services.pronunciation import (
    PronunciationSelection,
    apply_local,
)
from app.services.fa_kara_text import normalize_language

router = APIRouter(prefix="/projects", tags=["pronunciation"])

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


def _require_japanese(document: dict) -> None:
    if normalize_language(document.get("project", {}).get("language")) == "cn":
        raise HTTPException(422, "中文工程不需要注音")


@router.post("/{project_id}/pronunciation/local")
def local_pronunciation(project_id: str, payload: PronunciationRequest, request: Request):
    db, project, document = _project(request, project_id)
    _require_japanese(document)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    selection = _selection(document, payload)
    updated, summary = apply_local(document, selection)
    try:
        saved = db.save_document(project_id, updated, payload.revision)
    except ValueError as exc:
        raise HTTPException(409, {"code": "revision_conflict"}) from exc
    return {"revision": saved["revision"], "document": updated, "summary": summary}
