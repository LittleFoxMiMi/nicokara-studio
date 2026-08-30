from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.database import Database
from app.services.alignment import AlignmentQualityError, align_document
from app.services.audio import AudioProcessingError, prepare_source_audio
from app.services.separation import Kara2Separator
from app.services.transcription import FasterWhisperTranscriber
from app.media.waveform import generate_waveform


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class JobCanceled(RuntimeError):
    pass


class AnalysisPipeline:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.separator = Kara2Separator(settings.models_dir / "separator", settings.ffmpeg_path)
        self.transcriber = FasterWhisperTranscriber(download_root=settings.models_dir / "whisper")

    def _progress(self, job_id: str, progress: float, stage: str, message: str) -> None:
        job = self.database.get_job(job_id)
        if not job:
            raise JobCanceled()
        if job["cancel_requested"]:
            raise JobCanceled()
        self.database.update_job(job_id, status="RUNNING", progress=progress, stage=stage, message=message)

    def _paths(self, project_id: str) -> tuple[Path, Path, Path]:
        directory = self.settings.projects_dir / project_id
        return directory / "video.mp4", directory / "derived", directory / "derived" / "transcript.json"

    def process(self, job_id: str) -> dict:
        job = self.database.get_job(job_id)
        if not job:
            raise ValueError("job_not_found")
        project_id = job["project_id"]
        payload = job["request"]
        global_settings = self.database.settings()
        proxy_url = str(global_settings.get("proxy_url") or "").strip()
        if global_settings.get("proxy_enabled", True) and proxy_url:
            os.environ["HTTP_PROXY"] = proxy_url
            os.environ["HTTPS_PROXY"] = proxy_url
            os.environ["ALL_PROXY"] = proxy_url
        elif not global_settings.get("proxy_enabled", True):
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                os.environ.pop(key, None)
        video, derived, transcript_path = self._paths(project_id)
        if not video.is_file():
            raise AudioProcessingError("工程尚未上传视频")
        document = self.database.document(project_id, job["input_revision"])
        if not document:
            raise ValueError("input_revision_not_found")
        working_revision = job["input_revision"]
        self._progress(job_id, 0.05, "PREPARING", "正在准备 44.1 kHz 双声道音频")
        source_audio, source_asr = prepare_source_audio(video, derived, self.settings.ffmpeg_path)
        if job["type"] == "VOCAL_SEPARATION":
            self._progress(job_id, 0.25, "SEPARATING", "正在使用 KARA2 分离双 stem")
            vocals, instrumental, vocals_asr = self.separator.separate(
                source_audio,
                derived,
                model=payload.get("model") or global_settings.get("separator_model") or self.settings.separator_model,
                device=payload.get("device") or global_settings.get("separator_device") or self.settings.separator_device,
            )
            vocal_waveform = derived / "vocal_waveform.json"
            if not generate_waveform(vocals, vocal_waveform, self.settings.ffmpeg_path):
                raise AudioProcessingError("无法生成人声波形")
            document["media"] = {
                **document.get("media", {}),
                "waveform_source": "vocals",
                "waveform_url": f"/api/projects/{project_id}/waveform",
                "vocal_waveform_generated": True,
            }
            saved = self.database.save_document(project_id, document, working_revision)
            return {
                "source_audio": source_audio.name,
                "vocals": vocals.name,
                "instrumental": instrumental.name,
                "vocals_asr": vocals_asr.name,
                "vocal_waveform": vocal_waveform.name,
                "output_revision": saved["revision"],
            }
        vocals_asr = derived / "vocals_asr.wav"
        if not vocals_asr.is_file() or vocals_asr.stat().st_size == 0:
            self._progress(job_id, 0.2, "SEPARATING", "正在使用 KARA2 分离双 stem")
            _, _, vocals_asr = self.separator.separate(
                source_audio,
                derived,
                model=payload.get("separator_model") or global_settings.get("separator_model") or self.settings.separator_model,
                device=payload.get("separator_device") or global_settings.get("separator_device") or self.settings.separator_device,
            )
        vocal_waveform = derived / "vocal_waveform.json"
        if not vocal_waveform.is_file() and not generate_waveform(derived / "vocals.wav", vocal_waveform, self.settings.ffmpeg_path):
            raise AudioProcessingError("无法生成人声波形")
        document["media"] = {
            **document.get("media", {}),
            "waveform_source": "vocals",
            "waveform_url": f"/api/projects/{project_id}/waveform",
            "vocal_waveform_generated": True,
        }
        separation_saved = self.database.save_document(project_id, document, working_revision)
        working_revision = separation_saved["revision"]
        self._progress(job_id, 0.55, "TRANSCRIBING", "正在使用 Whisper 识别主唱")
        start_ms = payload.get("start_ms")
        end_ms = payload.get("end_ms")
        context_ms = 3000
        transcript = self.transcriber.transcribe(
            vocals_asr if vocals_asr.is_file() else source_asr,
            model_name=payload.get("model") or global_settings.get("whisper_model") or self.settings.whisper_model,
            device=payload.get("device") or global_settings.get("whisper_device") or self.settings.whisper_device,
            compute_type=payload.get("compute_type") or global_settings.get("whisper_compute_type") or self.settings.whisper_compute_type,
            start_ms=max(0, start_ms - context_ms) if isinstance(start_ms, int) else None,
            end_ms=end_ms + context_ms if isinstance(end_ms, int) else None,
        )
        derived.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._progress(job_id, 0.82, "ALIGNING", "正在按稳定歌词 ID 对齐时间")
        updated, summary = align_document(
            document,
            transcript,
            line_ids=payload.get("line_ids"),
            unit_ids=payload.get("unit_ids"),
            start_ms=start_ms,
            end_ms=end_ms,
            preserve_line_anchors=bool(payload.get("preserve_line_anchors", True)),
            overwrite_locked=payload.get("overwrite_policy") == "all",
        )
        updated.setdefault("analysis", {})["last_transcription"] = {
            "job_id": job_id,
            "model": payload.get("model") or global_settings.get("whisper_model") or self.settings.whisper_model,
            "scope": {"line_ids": payload.get("line_ids", []), "start_ms": start_ms, "end_ms": end_ms},
            **summary,
        }
        try:
            saved = self.database.save_document(project_id, updated, working_revision)
        except ValueError as exc:
            if str(exc).startswith("revision_conflict"):
                raise RuntimeError("revision_conflict") from exc
            raise
        return {**summary, "output_revision": saved["revision"]}


class AnalysisRunner:
    def __init__(self, database: Database, pipeline: AnalysisPipeline, max_workers: int = 1) -> None:
        self.database = database
        self.pipeline = pipeline
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nicokara-analysis")

    def enqueue(self, project_id: str, job_type: str, input_revision: int, payload: dict) -> dict:
        job_id = str(uuid4())
        job = self.database.create_job(job_id, project_id, job_type, input_revision, payload)
        self.executor.submit(self._run, job_id)
        return job

    def _run(self, job_id: str) -> None:
        self.database.update_job(job_id, status="PREPARING", started_at=timestamp(), message="任务已开始")
        try:
            result = self.pipeline.process(job_id)
            self.database.update_job(
                job_id,
                status="SUCCEEDED",
                progress=1.0,
                stage="COMPLETED",
                message="处理完成",
                result_json=result,
                output_revision=result.get("output_revision"),
                completed_at=timestamp(),
            )
        except JobCanceled:
            self.database.update_job(job_id, status="CANCELED", message="任务已取消", completed_at=timestamp())
        except (AudioProcessingError, AlignmentQualityError) as exc:
            self.database.update_job(job_id, status="FAILED", error_code="analysis_failed", error_message=str(exc), message="处理失败", completed_at=timestamp())
        except RuntimeError as exc:
            code = "revision_conflict" if str(exc) == "revision_conflict" else "runtime_error"
            message = "工程已被编辑，分析结果未覆盖当前版本" if code == "revision_conflict" else str(exc)
            self.database.update_job(job_id, status="FAILED", error_code=code, error_message=message, message="处理失败", completed_at=timestamp())
        except Exception:
            self.database.update_job(job_id, status="FAILED", error_code="internal_error", error_message="分析任务发生内部错误，请查看后端日志", message="处理失败", completed_at=timestamp())

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
