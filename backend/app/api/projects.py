from __future__ import annotations
import re
import shutil
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from app.core.database import Database
from app.core.config import Settings
from app.domain.lyrics import detect_lyrics_format, parse_lyrics
from app.media.probe import probe, thumbnail
from app.media.waveform import generate_waveform
from app.schemas.projects import AnalysisRequest, DocumentSave, LyricsDetect, LyricsImport, ProjectCreate, ProjectPatch, ProjectResponse, SeparationRequest

router = APIRouter(prefix="/projects", tags=["projects"])

def services(request: Request) -> tuple[Settings, Database]:
    return request.app.state.settings, request.app.state.database

def project_or_404(db: Database, project_id: str) -> dict:
    try: UUID(project_id)
    except ValueError as exc: raise HTTPException(404, "项目不存在") from exc
    project = db.get_project(project_id)
    if not project: raise HTTPException(404, "项目不存在")
    return project

def initial_document(project_id: str, name: str) -> dict:
    return {"schema_version": 1, "project": {"id": project_id, "name": name, "title": "", "artist": "", "revision": 1}, "media": {"video_asset_id": None, "duration_ms": None, "width": None, "height": None, "fps": None, "video_filename": None, "thumbnail_url": None}, "lyrics": {"source_type": "manual", "lines": []}, "styles": {}, "layout": {}, "export_presets": []}

@router.get("", response_model=list[ProjectResponse])
def list_projects(request: Request): return services(request)[1].list_projects()

@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(payload: ProjectCreate, request: Request):
    project_id = str(uuid4()); name = payload.name.strip()
    return services(request)[1].create_project(project_id, name, initial_document(project_id, name))

@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, request: Request): return project_or_404(services(request)[1], project_id)

@router.patch("/{project_id}", response_model=ProjectResponse)
def patch_project(project_id: str, payload: ProjectPatch, request: Request):
    db = services(request)[1]; project = project_or_404(db, project_id)
    doc = db.document(project_id) or initial_document(project_id, project["name"])
    if payload.name is not None: doc["project"]["name"] = payload.name.strip()
    if payload.title is not None: doc["project"]["title"] = payload.title
    if payload.artist is not None: doc["project"]["artist"] = payload.artist
    db.save_document(project_id, doc, project["revision"]); return db.get_project(project_id)

@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, request: Request):
    settings, db = services(request); project_or_404(db, project_id)
    db.delete_project(project_id)
    shutil.rmtree(settings.projects_dir / project_id, ignore_errors=True)

@router.get("/{project_id}/document")
def get_document(project_id: str, request: Request):
    db = services(request)[1]; project = project_or_404(db, project_id)
    return {"revision": project["revision"], "document": db.document(project_id)}

@router.put("/{project_id}/document")
def save_document(project_id: str, payload: DocumentSave, request: Request):
    db = services(request)[1]; project_or_404(db, project_id)
    try: return db.save_document(project_id, payload.document, payload.revision)
    except ValueError as exc:
        raise HTTPException(409, {"code": "revision_conflict"}) from exc

@router.post("/{project_id}/lyrics/detect")
def detect_lyrics(project_id: str, payload: LyricsDetect, request: Request):
    project_or_404(services(request)[1], project_id)
    return detect_lyrics_format(payload.content, payload.filename).as_dict()

@router.post("/{project_id}/lyrics/import")
def import_lyrics(project_id: str, payload: LyricsImport, request: Request):
    db = services(request)[1]
    project = project_or_404(db, project_id)
    document = db.document(project_id) or initial_document(project_id, project["name"])
    duration = document.get("media", {}).get("duration_ms")
    try:
        document["lyrics"] = parse_lyrics(
            payload.content,
            payload.format,
            filename=payload.filename,
            media_duration_ms=duration if isinstance(duration, int) else None,
        )
        result = db.save_document(project_id, document, payload.revision)
    except ValueError as exc:
        if str(exc).startswith("revision_conflict"):
            raise HTTPException(409, {"code": "revision_conflict"}) from exc
        raise HTTPException(422, str(exc)) from exc
    return {"revision": result["revision"], "document": document, "job": None}

@router.post("/{project_id}/video")
async def upload_video(project_id: str, request: Request, video: UploadFile = File(...)):
    settings, db = services(request); project = project_or_404(db, project_id)
    name = Path(video.filename or "video.mp4").name; safe = re.sub(r"[^A-Za-z0-9._() -]", "_", name)[:180]
    if Path(safe).suffix.lower() != ".mp4": raise HTTPException(415, "第一版仅支持 MP4")
    directory = settings.projects_dir / project_id; directory.mkdir(parents=True, exist_ok=True); destination = directory / "video.mp4"; total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_video_bytes: raise HTTPException(413, "视频文件超过大小限制")
                output.write(chunk)
    finally: await video.close()
    metadata = probe(destination, settings.ffprobe_path); thumb = directory / "thumbnail.jpg"; metadata["thumbnail_generated"] = thumbnail(destination, thumb, settings.ffmpeg_path)
    metadata["waveform_generated"] = generate_waveform(destination, directory / "waveform.json", settings.ffmpeg_path)
    metadata["waveform_url"] = f"/api/projects/{project_id}/waveform" if metadata["waveform_generated"] else None
    doc = db.document(project_id) or initial_document(project_id, project["name"]); doc["media"] = {**doc.get("media", {}), **metadata, "video_filename": safe, "thumbnail_url": f"/api/projects/{project_id}/thumbnail"}
    result = db.save_document(project_id, doc, project["revision"])
    return {"revision": result["revision"], "media": doc["media"], "job": None}

@router.post("/{project_id}/separate", status_code=202)
def separate(project_id: str, payload: SeparationRequest, request: Request):
    db = services(request)[1]
    project = project_or_404(db, project_id)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    document = db.document(project_id)
    if not document or not document.get("media", {}).get("video_filename"):
        raise HTTPException(422, "请先上传视频")
    return request.app.state.analysis_runner.enqueue(project_id, "VOCAL_SEPARATION", payload.revision, payload.model_dump())

@router.post("/{project_id}/transcribe", status_code=202)
def transcribe(project_id: str, payload: AnalysisRequest, request: Request):
    db = services(request)[1]
    project = project_or_404(db, project_id)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    document = db.document(project_id)
    if not document or not document.get("media", {}).get("video_filename"):
        raise HTTPException(422, "请先上传视频")
    lines = document.get("lyrics", {}).get("lines", [])
    known = {line["id"] for line in lines}
    if payload.line_ids and not set(payload.line_ids).issubset(known):
        raise HTTPException(422, "识别范围包含未知歌词行")
    if payload.start_ms is not None and payload.end_ms is not None and payload.end_ms <= payload.start_ms:
        raise HTTPException(422, "识别结束时间必须晚于开始时间")
    return request.app.state.analysis_runner.enqueue(project_id, "TRANSCRIPTION", payload.revision, payload.model_dump())

@router.get("/{project_id}/video")
def get_video(project_id: str, request: Request):
    settings, db = services(request); project_or_404(db, project_id); path = settings.projects_dir / project_id / "video.mp4"
    if not path.is_file(): raise HTTPException(404, "视频不存在")
    return FileResponse(path, media_type="video/mp4")

@router.get("/{project_id}/thumbnail")
def get_thumbnail(project_id: str, request: Request):
    settings, db = services(request); project_or_404(db, project_id); path = settings.projects_dir / project_id / "thumbnail.jpg"
    if not path.is_file(): raise HTTPException(404, "缩略图不存在")
    return FileResponse(path, media_type="image/jpeg")

@router.get("/{project_id}/waveform")
def get_waveform(project_id: str, request: Request):
    settings, db = services(request)
    project_or_404(db, project_id)
    directory = settings.projects_dir / project_id
    path = directory / "waveform.json"
    vocal_path = directory / "derived" / "vocal_waveform.json"
    if vocal_path.is_file():
        path = vocal_path
    if not path.is_file():
        video = directory / "video.mp4"
        if not video.is_file():
            raise HTTPException(404, "波形不存在")
        if not generate_waveform(video, path, settings.ffmpeg_path):
            raise HTTPException(503, "波形生成失败")
    return FileResponse(path, media_type="application/json")
