from __future__ import annotations
import copy
import re
import shutil
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from app.core.database import Database
from app.core.config import Settings
from app.domain.lyrics import detect_lyrics_format, parse_lyrics
from app.media.probe import probe, thumbnail
from app.media.waveform import generate_waveform
from app.services.audio import prepare_source_audio
from app.schemas.projects import AnalysisRequest, DocumentSave, ExportRequest, FAKaraRequest, FullAnalysisRequest, LyricsDetect, LyricsImport, ProjectCreate, ProjectPatch, ProjectResponse, SeparationRequest
from app.services.kirakara_export import build_worker_html, export_output_path, export_raw_output_path
from app.services.fa_kara_text import missing_japanese_ruby, normalize_language

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
    return {"schema_version": 1, "project": {"id": project_id, "name": name, "title": "", "artist": "", "language": "jp", "revision": 1}, "media": {"video_asset_id": None, "duration_ms": None, "width": None, "height": None, "fps": None, "video_filename": None, "thumbnail_url": None}, "lyrics": {"source_type": "manual", "lines": []}, "styles": {}, "layout": {}, "export_presets": []}


def document_language(document: dict) -> str:
    return normalize_language(document.get("project", {}).get("language"))


def _missing_ruby_message(document: dict) -> str | None:
    missing = missing_japanese_ruby(document.get("lyrics", {}).get("lines", []))
    if document_language(document) != "jp" or not missing:
        return None
    labels = [f"第 {item['line_index'] + 1} 行：{item['characters']}" for item in missing]
    return "以下日文汉字尚未注音：" + "；".join(labels) + "。请先完成本地注音或 AI 注音。"


def normalize_document(document: dict) -> dict:
    language = document_language(document)
    document.setdefault("project", {})["language"] = language
    if language == "cn":
        for line in document.get("lyrics", {}).get("lines", []):
            for unit in line.get("units", []):
                unit["ruby"] = None
                unit["ruby_2"] = None
                unit["ruby_source"] = "none"
                unit.pop("ruby_span", None)
                unit.pop("ruby_confidence", None)
    return document

@router.get("", response_model=list[ProjectResponse])
def list_projects(request: Request): return services(request)[1].list_projects()

@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(payload: ProjectCreate, request: Request):
    project_id = str(uuid4()); name = payload.name.strip()
    return services(request)[1].create_project(project_id, name, initial_document(project_id, name))

@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, request: Request): return project_or_404(services(request)[1], project_id)

@router.post("/{project_id}/copy", response_model=ProjectResponse, status_code=201)
def copy_project(project_id: str, request: Request, payload: ProjectCreate | None = None):
    settings, db = services(request)
    source = project_or_404(db, project_id)
    source_document = db.document(project_id)
    if not source_document:
        raise HTTPException(404, "工程文档不存在")
    copied_id = str(uuid4())
    copied_name = (payload.name.strip() if payload and payload.name else f"{source['name']} (副本)")
    if not copied_name:
        raise HTTPException(422, "复制工程名称不能为空")
    document = copy.deepcopy(source_document)
    document.setdefault("project", {}).update({"id": copied_id, "name": copied_name, "revision": 1})
    media = document.setdefault("media", {})
    media["thumbnail_url"] = f"/api/projects/{copied_id}/thumbnail" if media.get("video_filename") else None
    media["waveform_url"] = f"/api/projects/{copied_id}/waveform" if media.get("waveform_url") or media.get("waveform_source") or media.get("waveform_generated") else None
    if isinstance(document.get("analysis"), dict):
        for result in document["analysis"].values():
            if isinstance(result, dict):
                result.pop("job_id", None)
    source_dir = settings.projects_dir / project_id
    target_dir = settings.projects_dir / copied_id
    try:
        db.clone_project_record(copied_id, copied_name, document)
        if source_dir.is_dir():
            shutil.copytree(source_dir, target_dir, ignore=shutil.ignore_patterns("exports"))
    except Exception:
        db.delete_project(copied_id)
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(500, "复制工程失败")
    return db.get_project(copied_id)

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
    try: return db.save_document(project_id, normalize_document(payload.document), payload.revision)
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
        normalize_document(document)
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

@router.post("/{project_id}/fa-kara", status_code=202)
def fa_kara(project_id: str, payload: FAKaraRequest, request: Request):
    settings, db = services(request)
    project = project_or_404(db, project_id)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    document = db.document(project_id)
    if not document or not document.get("media", {}).get("video_filename"):
        raise HTTPException(422, "请先上传视频")
    if not document.get("lyrics", {}).get("lines"):
        raise HTTPException(422, "请先添加歌词")
    if message := _missing_ruby_message(document):
        raise HTTPException(422, message)
    if not (settings.projects_dir / project_id / "derived" / "vocals_asr.wav").is_file():
        raise HTTPException(422, "请先完成 KARA2 人声分离")
    completion = _analysis_completion(document, project_id, settings)
    if not completion["transcription"]:
        raise HTTPException(422, "请先完成 Whisper 粗识别")
    if not completion["pronunciation"]:
        raise HTTPException(422, "请先完成 AI 注音或本地注音")
    model = payload.model or str(db.settings().get("fa_kara_model") or "mms")
    return request.app.state.analysis_runner.enqueue(
        project_id,
        "FA_KARA_ALIGNMENT",
        payload.revision,
        {**payload.model_dump(), "model": model, "steps": ["fa_kara"]},
    )


def _analysis_completion(document: dict, project_id: str, settings: Settings) -> dict[str, bool]:
    derived = settings.projects_dir / project_id / "derived"
    lines = document.get("lyrics", {}).get("lines", [])
    has_kanji = any("一" <= char <= "龯" for line in lines for unit in line.get("units", []) for char in str(unit.get("surface", "")))
    analysis = document.get("analysis", {})
    language = document_language(document)
    return {
        "separation": (derived / "vocals_asr.wav").is_file(),
        "transcription": (derived / "transcript.json").is_file() and analysis.get("transcription", {}).get("status") == "completed",
        "pronunciation": language == "cn" or (not has_kanji) or analysis.get("pronunciation", {}).get("status") == "completed" or document.get("pronunciation", {}).get("last_run", {}).get("mode") in {"local", "ai", "local_fallback"},
        "global_alignment": (derived / "stable_global.json").is_file() and bool(lines) and all(line.get("start_ms") is not None and line.get("end_ms") is not None for line in lines) and analysis.get("global_alignment", {}).get("status") == "completed",
        "alignment": analysis.get("alignment", {}).get("status") == "completed",
        "fa_kara": (derived / "fa_kara.json").is_file() and analysis.get("fa_kara", {}).get("status") == "completed",
    }


def _validate_full_steps(
    project: dict,
    document: dict,
    project_id: str,
    payload: FullAnalysisRequest,
    settings: Settings,
    alignment_backend: str,
) -> None:
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    if not document.get("media", {}).get("video_filename"):
        raise HTTPException(422, "请先上传视频")
    language = document_language(document)
    steps = list(dict.fromkeys(payload.steps))
    if language == "cn":
        steps = [step for step in steps if step != "pronunciation"]
    if not steps:
        raise HTTPException(422, "至少选择一个分析流程")
    complete = _analysis_completion(document, project_id, settings)
    order = (
        (["separation", "transcription", "fa_kara"] if language == "cn" else ["separation", "transcription", "pronunciation", "fa_kara"])
        if alignment_backend == "fa_kara"
        else (["separation", "transcription", "global_alignment", "alignment"] if language == "cn" else ["separation", "transcription", "pronunciation", "global_alignment", "alignment"])
    )
    unexpected = [key for key in steps if key not in order]
    if unexpected:
        raise HTTPException(422, f"当前对齐后端不支持流程：{unexpected[0]}")
    for key in order:
        if key not in steps and not complete[key]:
            raise HTTPException(422, f"不能跳过未完成的流程：{key}")
        if key in steps:
            previous = order[:order.index(key)]
            missing = [item for item in previous if item not in steps and not complete[item]]
            if missing:
                raise HTTPException(422, f"{key} 的前置流程尚未完成：{missing[0]}")
    if any(step in {"fa_kara", "global_alignment", "alignment"} for step in steps):
        if message := _missing_ruby_message(document):
            raise HTTPException(422, message)


@router.post("/{project_id}/analysis", status_code=202)
def full_analysis(project_id: str, payload: FullAnalysisRequest, request: Request):
    settings, db = services(request)
    project = project_or_404(db, project_id)
    document = db.document(project_id)
    if not document:
        raise HTTPException(404, "工程文档不存在")
    configured = db.settings()
    alignment_backend = payload.alignment_backend or str(configured.get("alignment_backend") or "fa_kara")
    if document_language(document) == "cn":
        alignment_backend = "fa_kara"
    _validate_full_steps(project, document, project_id, payload, settings, alignment_backend)
    body = payload.model_dump()
    body["steps"] = list(dict.fromkeys(payload.steps))
    if document_language(document) == "cn":
        body["steps"] = [step for step in body["steps"] if step != "pronunciation"]
    body["alignment_backend"] = alignment_backend
    body["fa_kara_model"] = payload.fa_kara_model or str(configured.get("fa_kara_model") or "mms")
    return request.app.state.analysis_runner.enqueue(project_id, "FULL_ANALYSIS", payload.revision, body)


@router.post("/{project_id}/align-global", status_code=202)
def align_global(project_id: str, payload: AnalysisRequest, request: Request):
    settings, db = services(request)
    project = project_or_404(db, project_id)
    document = db.document(project_id)
    if not document:
        raise HTTPException(404, "工程文档不存在")
    if document_language(document) == "cn":
        raise HTTPException(422, "中文工程请使用 FA-Kara 对齐")
    if message := _missing_ruby_message(document):
        raise HTTPException(422, message)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    completion = _analysis_completion(document, project_id, settings)
    if not completion["transcription"]:
        raise HTTPException(422, "请先完成 Whisper 粗识别")
    if not completion["pronunciation"]:
        raise HTTPException(422, "请先完成 AI 注音或本地注音")
    return request.app.state.analysis_runner.enqueue(project_id, "STABLE_GLOBAL_ALIGNMENT", payload.revision, payload.model_dump())


@router.post("/{project_id}/align", status_code=202)
def align(project_id: str, payload: AnalysisRequest, request: Request):
    settings, db = services(request)
    project = project_or_404(db, project_id)
    document = db.document(project_id)
    if not document:
        raise HTTPException(404, "工程文档不存在")
    if document_language(document) == "cn":
        raise HTTPException(422, "中文工程请使用 FA-Kara 对齐")
    if message := _missing_ruby_message(document):
        raise HTTPException(422, message)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    completion = _analysis_completion(document, project_id, settings)
    scoped_line = next((line for line in document.get("lyrics", {}).get("lines", []) if payload.line_ids == [line.get("id")]), None)
    if not completion["global_alignment"] and not (
        scoped_line and scoped_line.get("start_ms") is not None and scoped_line.get("end_ms") is not None
    ):
        raise HTTPException(422, "请先完成 stable-ts 全局对齐")
    return request.app.state.analysis_runner.enqueue(project_id, "STABLE_ALIGNMENT", payload.revision, payload.model_dump())


@router.post("/{project_id}/pronunciation-job", status_code=202)
def pronunciation_job(project_id: str, payload: dict, request: Request):
    db = services(request)[1]
    project = project_or_404(db, project_id)
    document = db.document(project_id)
    if not document or not document.get("lyrics", {}).get("lines"):
        raise HTTPException(422, "请先添加歌词")
    if project["revision"] != payload.get("revision"):
        raise HTTPException(409, {"code": "revision_conflict"})
    if document_language(document) == "cn":
        raise HTTPException(422, "中文工程不需要注音")
    body = {"revision": project["revision"], "line_ids": payload.get("line_ids", []), "unit_ids": payload.get("unit_ids", []), "overwrite_policy": payload.get("overwrite_policy", "unlocked_only"), "profile_id": payload.get("profile_id"), "mode": payload.get("mode", "ai"), "steps": ["pronunciation"]}
    return request.app.state.analysis_runner.enqueue(project_id, "PRONUNCIATION", project["revision"], body)

@router.post("/{project_id}/export", status_code=202)
def export_project(project_id: str, payload: ExportRequest, request: Request):
    db = services(request)[1]
    project = project_or_404(db, project_id)
    if project["revision"] != payload.revision:
        raise HTTPException(409, {"code": "revision_conflict"})
    document = db.document(project_id)
    if not document or not document.get("media", {}).get("video_filename"):
        raise HTTPException(422, "导出请先上传视频")
    if payload.audio_track == "off_vocal" and not (services(request)[0].projects_dir / project_id / "derived" / "instrumental.wav").is_file():
        raise HTTPException(422, "请先完成 KARA2 人声分离")
    return request.app.state.analysis_runner.enqueue(project_id, "EXPORT", payload.revision, {**payload.model_dump(), "steps": ["export"]})

@router.get("/{project_id}/exports")
def list_exports(project_id: str, request: Request, limit: int = Query(20, ge=1, le=100)):
    db = services(request)[1]
    project_or_404(db, project_id)
    return [job for job in db.list_jobs(project_id, limit) if job["type"] == "EXPORT"]

@router.get("/{project_id}/exports/{job_id}/download")
def download_export(project_id: str, job_id: str, request: Request):
    settings, db = services(request)
    project_or_404(db, project_id)
    job = db.get_job(job_id)
    if not job or job["project_id"] != project_id or job["type"] != "EXPORT" or job["status"] != "SUCCEEDED":
        raise HTTPException(404, "导出文件不存在")
    result = job.get("result") or {}
    path = export_output_path(settings, project_id, job_id, str(result.get("format", job.get("request", {}).get("format", "mp4"))))
    if not path.is_file():
        raise HTTPException(404, "导出文件已被清理")
    filename = result.get("filename") or f"{project_id}.{path.suffix.lstrip('.') }"
    return FileResponse(path, media_type="video/mp4" if path.suffix == ".mp4" else "video/webm", filename=filename)

@router.delete("/{project_id}/exports/{job_id}", status_code=204)
def delete_export(project_id: str, job_id: str, request: Request):
    settings, db = services(request)
    project_or_404(db, project_id)
    job = db.get_job(job_id)
    if not job or job["project_id"] != project_id or job["type"] != "EXPORT":
        raise HTTPException(404, "导出记录不存在")
    if job["status"] in {"QUEUED", "PREPARING", "RUNNING"}:
        raise HTTPException(409, "导出任务仍在运行，完成或取消后才能删除")
    fmt = str(job.get("request", {}).get("format", "mp4"))
    output = export_output_path(settings, project_id, job_id, fmt)
    for path in (output, export_raw_output_path(settings, project_id, job_id, fmt), output.with_suffix(".error")):
        path.unlink(missing_ok=True)
    if not db.delete_job(job_id, project_id):
        raise HTTPException(404, "导出记录不存在")

@router.get("/{project_id}/export-worker/{job_id}", response_class=HTMLResponse)
def export_worker(project_id: str, job_id: str, request: Request):
    settings, db = services(request)
    project_or_404(db, project_id)
    job = db.get_job(job_id)
    if not job or job["project_id"] != project_id or job["type"] != "EXPORT":
        raise HTTPException(404, "导出任务不存在")
    document = db.document(project_id, job["input_revision"])
    if not document:
        raise HTTPException(404, "工程文档不存在")
    return HTMLResponse(build_worker_html(settings, project_id, job_id, document, job["request"]))

@router.post("/{project_id}/export-worker/{job_id}/progress")
async def export_worker_progress(project_id: str, job_id: str, request: Request):
    db = services(request)[1]
    job = db.get_job(job_id)
    if not job or job["project_id"] != project_id or job["type"] != "EXPORT":
        raise HTTPException(404, "导出任务不存在")
    body = await request.json()
    value = max(0.0, min(1.0, float(body.get("progress", 0))))
    message = str(body.get("message") or "正在渲染 Kirakara")
    steps = job.get("steps", [])
    for step in steps:
        if step.get("key") == "export":
            step.update(progress=round(value, 4), status="completed" if value >= 1 else "running", label="Kirakara 服务端导出", message=message)
    db.update_job(job_id, status="RUNNING", progress=value, stage="EXPORT", message=message, steps=steps)
    return {"ok": True}

@router.get("/{project_id}/export-worker/{job_id}/cancel")
def export_worker_cancel(project_id: str, job_id: str, request: Request):
    db = services(request)[1]
    job = db.get_job(job_id)
    if not job or job["project_id"] != project_id or job["type"] != "EXPORT":
        raise HTTPException(404, "导出任务不存在")
    return {"cancel_requested": bool(job["cancel_requested"])}

@router.post("/{project_id}/export-worker/{job_id}/result")
async def export_worker_result(project_id: str, job_id: str, request: Request):
    settings, db = services(request)
    job = db.get_job(job_id)
    if not job or job["project_id"] != project_id or job["type"] != "EXPORT":
        raise HTTPException(404, "导出任务不存在")
    fmt = str(job["request"].get("format", "mp4"))
    path = export_raw_output_path(settings, project_id, job_id, fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(await request.body())
    temporary.replace(path)
    return {"ok": True, "filename": request.headers.get("x-output-filename") or path.name}

@router.post("/{project_id}/export-worker/{job_id}/error")
async def export_worker_error(project_id: str, job_id: str, request: Request):
    settings, db = services(request)
    job = db.get_job(job_id)
    if not job or job["project_id"] != project_id or job["type"] != "EXPORT":
        raise HTTPException(404, "导出任务不存在")
    marker = export_output_path(settings, project_id, job_id, str(job["request"].get("format", "mp4"))).with_suffix(".error")
    marker.write_text((await request.body()).decode("utf-8", "replace")[:4000], encoding="utf-8")
    return {"ok": True}

@router.get("/{project_id}/video")
def get_video(project_id: str, request: Request):
    settings, db = services(request); project_or_404(db, project_id); path = settings.projects_dir / project_id / "video.mp4"
    if not path.is_file(): raise HTTPException(404, "视频不存在")
    return FileResponse(path, media_type="video/mp4")

@router.get("/{project_id}/audio")
def get_audio(project_id: str, request: Request, track: str = Query("on_vocal", pattern="^(on_vocal|off_vocal)$")):
    settings, db = services(request)
    project_or_404(db, project_id)
    directory = settings.projects_dir / project_id
    video = directory / "video.mp4"
    if not video.is_file():
        raise HTTPException(404, "视频不存在")
    derived = directory / "derived"
    if track == "on_vocal":
        try:
            path, _ = prepare_source_audio(video, derived, settings.ffmpeg_path)
        except Exception as exc:
            raise HTTPException(503, "无法准备原始音轨") from exc
    else:
        path = derived / "instrumental.wav"
    if not path.is_file():
        raise HTTPException(404, "指定音轨不存在，请先完成 KARA2 分离")
    return FileResponse(path, media_type="audio/wav")

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
