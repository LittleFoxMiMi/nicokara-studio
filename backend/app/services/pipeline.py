from __future__ import annotations

import json
import logging
import os
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.database import Database
from app.media.waveform import generate_waveform
from app.services.alignment import AlignmentQualityError
from app.services.audio import AudioProcessingError, convert_audio, extract_audio_clip, prepare_source_audio
from app.services.cancellation import OperationCanceled
from app.services.pronunciation import PronunciationSelection, PronunciationValidationError, apply_local, run_ai_pronunciation
from app.services.fa_kara_text import missing_japanese_ruby, normalize_language
from app.services.separation import VocalSeparator
from app.services.stable_ts import StableTSAligner, StableTSAlignmentError, rough_line_ranges
from app.services.fa_kara import FAKaraAligner
from app.services.transcription import FasterWhisperTranscriber, Transcript, TranscriptSegment, TranscriptWord
from app.services.kirakara_export import ExportCanceled, ExportError, run_kirakara_export
from app.services.model_runtime import ResidentModelStore
from app.services.japanese_phoneme import JapanesePhonemeRecognizer, MODEL_NAME as JAPANESE_PHONEME_MODEL, phoneme_result, split_phonemes_at_segment_starts


logger = logging.getLogger(__name__)


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class JobCanceled(RuntimeError):
    pass


class MissingRubyError(AlignmentQualityError):
    pass


STEP_LABELS = {
    "separation": "人声分离",
    "transcription": "Whisper 粗识别",
    "pronunciation": "AI 注音",
    "global_alignment": "stable-ts 全局对齐",
    "alignment": "stable-ts 词/短语精修",
    "fa_kara": "FA-Kara 对齐",
    "export": "Kirakara 服务端导出",
}


class AnalysisPipeline:
    def __init__(self, settings: Settings, database: Database, model_store: ResidentModelStore | None = None) -> None:
        self.settings = settings
        self.database = database
        self.model_store = model_store or ResidentModelStore()
        self.separator = VocalSeparator(settings.models_dir / "separator", settings.ffmpeg_path, model_store=self.model_store)
        self.transcriber = FasterWhisperTranscriber(download_root=settings.models_dir / "whisper", model_store=self.model_store)
        self.phoneme_recognizer = JapanesePhonemeRecognizer(cache_dir=settings.models_dir / "japanese-phoneme", model_store=self.model_store)

    def _step(self, job_id: str, key: str, progress: float, message: str, *, status: str | None = None) -> None:
        job = self.database.get_job(job_id)
        if not job or job["cancel_requested"]:
            raise JobCanceled()
        steps = job.get("steps", [])
        for item in steps:
            if item.get("key") == key:
                item["progress"] = round(max(0.0, min(1.0, progress)), 4)
                item["status"] = status or ("completed" if progress >= 1 else "running")
                item["message"] = message
                item["label"] = STEP_LABELS.get(key, key)
                break
        overall = sum(float(item.get("progress", 0)) for item in steps) / max(1, len(steps))
        self.database.update_job(job_id, status="RUNNING", progress=overall, stage=key.upper(), message=message, steps=steps)

    def _should_cancel(self, job_id: str) -> bool:
        job = self.database.get_job(job_id)
        return not job or bool(job["cancel_requested"])

    @staticmethod
    def _require_alignment_ruby(document: dict) -> None:
        if normalize_language(document.get("project", {}).get("language")) != "jp":
            return
        missing = missing_japanese_ruby(document.get("lyrics", {}).get("lines", []))
        if not missing:
            return
        labels = [f"第 {item['line_index'] + 1} 行：{item['characters']}" for item in missing]
        raise MissingRubyError(
            "AI 注音完成后仍有日文汉字未注音：" + "；".join(labels) + "。请选择本地注音后继续，或取消全曲分析。"
        )

    def _paths(self, project_id: str) -> tuple[Path, Path, Path]:
        directory = self.settings.projects_dir / project_id
        return directory / "video.mp4", directory / "derived", directory / "derived" / "transcript.json"

    def _settings(self) -> dict:
        values = self.database.settings()
        proxy_url = str(values.get("proxy_url") or "").strip()
        if values.get("proxy_enabled", True) and proxy_url:
            os.environ["HTTP_PROXY"] = proxy_url
            os.environ["HTTPS_PROXY"] = proxy_url
            os.environ["ALL_PROXY"] = proxy_url
        elif not values.get("proxy_enabled", True):
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                os.environ.pop(key, None)
        return values

    def _save(self, project_id: str, document: dict, revision: int) -> tuple[dict, int]:
        try:
            saved = self.database.save_document(project_id, document, revision)
        except ValueError as exc:
            if str(exc).startswith("revision_conflict"):
                raise RuntimeError("revision_conflict") from exc
            raise
        return saved["document"], saved["revision"]

    def _separate(self, job_id: str, project_id: str, document: dict, revision: int, payload: dict, values: dict) -> tuple[dict, int, dict]:
        video, derived, _ = self._paths(project_id)
        if not video.is_file():
            raise AudioProcessingError("工程尚未上传视频")
        self._step(job_id, "separation", 0.02, "正在准备 44.1 kHz 双声道音频")
        source_audio, _ = prepare_source_audio(video, derived, self.settings.ffmpeg_path)
        legacy_model = payload.get("separator_model") or payload.get("model") or values.get("separator_model") or self.settings.separator_model
        vocals_model = payload.get("separator_vocals_model") or values.get("separator_vocals_model") or legacy_model
        device = payload.get("separator_device") or payload.get("device") or values.get("separator_device") or self.settings.separator_device
        token = job_id.replace("-", "")[:12]
        staged_vocals_name = f"{token}_vocals.wav"
        staged_instrumental_name = f"{token}_unused_instrumental.wav"
        staged_asr_name = f"{token}_vocals_asr.wav"
        staged_paths = [
            derived / name for name in (
                staged_vocals_name,
                staged_instrumental_name,
                staged_asr_name,
            )
        ]
        self._step(job_id, "separation", 0.18, f"正在加载人声模型：{vocals_model}")
        try:
            staged_vocals, _, staged_asr = self.separator.separate(
                source_audio,
                derived,
                model=str(vocals_model),
                device=str(device),
                progress_callback=lambda value, message: self._step(job_id, "separation", 0.18 + value * 0.58, message),
                should_cancel=lambda: self._should_cancel(job_id),
                vocals_filename=staged_vocals_name,
                instrumental_filename=staged_instrumental_name,
                asr_filename=staged_asr_name,
            )
            if staged_asr is None:
                raise AudioProcessingError("无法生成 Whisper 人声音频")
            vocals = derived / "vocals.wav"
            vocals_asr = derived / "vocals_asr.wav"
            staged_vocals.replace(vocals)
            staged_asr.replace(vocals_asr)
        finally:
            for staged_path in staged_paths:
                staged_path.unlink(missing_ok=True)
        self._step(job_id, "separation", 0.78, "人声分离完成，正在生成 vocals 波形")
        vocal_waveform = derived / "vocal_waveform.json"
        if not generate_waveform(vocals, vocal_waveform, self.settings.ffmpeg_path):
            raise AudioProcessingError("无法生成人声波形")
        document["media"] = {**document.get("media", {}), "waveform_source": "vocals", "waveform_url": f"/api/projects/{project_id}/waveform", "vocal_waveform_generated": True}
        document.setdefault("analysis", {})["separation"] = {
            "status": "completed",
            "job_id": job_id,
            "vocals_model": vocals_model,
        }
        document, revision = self._save(project_id, document, revision)
        self._step(job_id, "separation", 1.0, "人声分离完成")
        return document, revision, {"source_audio": source_audio.name, "vocals": vocals.name, "vocals_asr": vocals_asr.name, "vocal_waveform": vocal_waveform.name, "vocals_model": vocals_model, "output_revision": revision}

    @staticmethod
    def _read_transcript(path: Path) -> Transcript:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Transcript(
            str(data.get("language", "ja")), float(data.get("language_probability", 0)), float(data.get("duration_seconds", 0)),
            [TranscriptSegment(int(item.get("id", index)), str(item.get("text", "")), int(item.get("start_ms", 0)), int(item.get("end_ms", 0)), float(item.get("confidence", 0)), float(item.get("no_speech_probability", 0)), [TranscriptWord(str(word.get("text", "")), int(word.get("start_ms", 0)), int(word.get("end_ms", 0)), float(word.get("confidence", 0))) for word in item.get("words", [])]) for index, item in enumerate(data.get("segments", []))],
        )

    def _transcribe(self, job_id: str, project_id: str, document: dict, revision: int, payload: dict, values: dict) -> tuple[dict, int, dict]:
        _, derived, transcript_path = self._paths(project_id)
        vocals_asr = derived / "vocals_asr.wav"
        source_asr = derived / "source_asr.wav"
        audio = vocals_asr if vocals_asr.is_file() else source_asr
        if not audio.is_file():
            raise AudioProcessingError("请先完成人声分离")
        self._step(job_id, "transcription", 0.02, "正在下载或加载 Whisper 模型")
        start_ms, end_ms = payload.get("start_ms"), payload.get("end_ms")
        transcript = self.transcriber.transcribe(
            audio,
            model_name=payload.get("whisper_model") or payload.get("model") or values.get("whisper_model") or self.settings.whisper_model,
            device=payload.get("whisper_device") or values.get("whisper_device") or self.settings.whisper_device,
            compute_type=payload.get("compute_type") or values.get("whisper_compute_type") or self.settings.whisper_compute_type,
            language=normalize_language(document.get("project", {}).get("language")),
            start_ms=max(0, start_ms - 3000) if isinstance(start_ms, int) else None,
            end_ms=end_ms + 3000 if isinstance(end_ms, int) else None,
            progress_callback=lambda value, message: self._step(job_id, "transcription", 0.08 + value * 0.48, message),
            should_cancel=lambda: self._should_cancel(job_id),
        )
        derived.mkdir(parents=True, exist_ok=True)
        transcript_data = transcript.to_dict()
        language = normalize_language(document.get("project", {}).get("language"))
        phoneme_count = 0
        if language == "jp":
            clip_start_ms = max(0, start_ms - 3000) if isinstance(start_ms, int) else None
            clip_end_ms = end_ms + 3000 if isinstance(end_ms, int) else None
            proxy_url = str(values.get("proxy_url") or "") if values.get("proxy_enabled", True) else None
            self._step(job_id, "transcription", 0.58, "正在下载或加载日语 HuBERT 音素模型")
            phones = self.phoneme_recognizer.recognize(
                audio,
                start_ms=clip_start_ms,
                end_ms=clip_end_ms,
                proxy_url=proxy_url,
                progress_callback=lambda value, message: self._step(job_id, "transcription", 0.58 + value * 0.38, message),
                should_cancel=lambda: self._should_cancel(job_id),
            )
            phoneme_count = len(phones)
            split_phonemes_at_segment_starts(transcript_data, phones)
            (derived / "japanese_phonemes.json").write_text(
                json.dumps(phoneme_result(phones), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        transcript_path.write_text(json.dumps(transcript_data, ensure_ascii=False, indent=2), encoding="utf-8")
        document.setdefault("analysis", {})["transcription"] = {"status": "completed", "job_id": job_id, "model": payload.get("whisper_model") or values.get("whisper_model") or self.settings.whisper_model, "scope": {"line_ids": payload.get("line_ids", []), "start_ms": start_ms, "end_ms": end_ms}, "segment_count": len(transcript.segments), "phoneme_model": JAPANESE_PHONEME_MODEL if language == "jp" else None, "phoneme_count": phoneme_count}
        if document.get("lyrics", {}).get("lines"):
            ranges = rough_line_ranges(document, transcript)
            for line in document["lyrics"]["lines"]:
                if line["id"] in ranges and (not payload.get("line_ids") or line["id"] in payload["line_ids"]):
                    line["start_ms"], line["end_ms"] = ranges[line["id"]]
                    line["timing_source"], line["timing_precision"] = "whisper_coarse", "line"
        document, revision = self._save(project_id, document, revision)
        self._step(job_id, "transcription", 1.0, "Whisper 与日语音素粗识别完成" if language == "jp" else "Whisper 人声粗识别完成")
        return document, revision, {"segment_count": len(transcript.segments), "phoneme_count": phoneme_count, "output_revision": revision}

    def _pronounce(self, job_id: str, project_id: str, document: dict, revision: int, payload: dict) -> tuple[dict, int, dict]:
        if normalize_language(document.get("project", {}).get("language")) == "cn":
            updated = copy.deepcopy(document)
            updated.setdefault("pronunciation", {})["last_run"] = {"mode": "skipped_cn", "applied": 0}
            updated.setdefault("analysis", {})["pronunciation"] = {"status": "completed", "job_id": job_id, "mode": "skipped_cn", "applied": 0}
            updated, revision = self._save(project_id, updated, revision)
            self._step(job_id, "pronunciation", 1.0, "中文工程跳过注音")
            return updated, revision, {"mode": "skipped_cn", "applied": 0, "output_revision": revision}
        selection = PronunciationSelection(payload.get("line_ids", []), payload.get("unit_ids", []), payload.get("overwrite_policy", "unlocked_only"))
        if payload.get("mode") == "local":
            self._step(job_id, "pronunciation", 0.15, "正在生成本地日语读音")
            updated, summary = apply_local(document, selection)
            self._step(job_id, "pronunciation", 1.0, "本地注音完成")
        else:
            _, _, transcript_path = self._paths(project_id)
            transcription = document.get("analysis", {}).get("transcription", {})
            if not transcript_path.is_file() or transcription.get("status") != "completed":
                raise PronunciationValidationError("AI 注音需要先完成 Whisper 粗识别")
            updated, summary = run_ai_pronunciation(database=self.database, settings=self.settings, project_id=project_id, document=document, selection=selection, profile_id=payload.get("profile_id"), progress_callback=lambda value, message: self._step(job_id, "pronunciation", value, message))
        updated.setdefault("analysis", {})["pronunciation"] = {"status": "completed", "job_id": job_id, **summary}
        updated, revision = self._save(project_id, updated, revision)
        self._step(job_id, "pronunciation", 1.0, "AI 注音完成")
        return updated, revision, summary | {"output_revision": revision}

    def _stable_aligner(self, job_id: str, step: str, model_name: str, values: dict) -> StableTSAligner:
        return StableTSAligner(
            lambda: self.transcriber.get_stable_model(
                model_name=model_name,
                device=values.get("whisper_device") or self.settings.whisper_device,
                compute_type=values.get("whisper_compute_type") or self.settings.whisper_compute_type,
                progress_callback=lambda value, message: self._step(job_id, step, 0.02 + value * 0.06, message),
                should_cancel=lambda: self._should_cancel(job_id),
            )
        )

    def _global_align(self, job_id: str, project_id: str, document: dict, revision: int, payload: dict, values: dict) -> tuple[dict, int, dict]:
        _, derived, _ = self._paths(project_id)
        audio = derived / "vocals_asr.wav"
        global_result = derived / "stable_global.json"
        if not audio.is_file():
            raise AudioProcessingError("请先完成人声分离")
        model_name = payload.get("whisper_model") or payload.get("model") or values.get("whisper_model") or self.settings.whisper_model
        stable = self._stable_aligner(job_id, "global_alignment", model_name, values)
        token_step = int(values.get("stable_ts_token_step", 100))
        updated, summary = stable.global_align(document, audio, token_step=token_step, result_path=global_result, progress_callback=lambda value, message: self._step(job_id, "global_alignment", value, message))
        updated.setdefault("analysis", {})["global_alignment"] = {"status": "completed", "job_id": job_id, **summary}
        # A new global result invalidates phrase timings based on old ranges.
        updated.setdefault("analysis", {}).pop("alignment", None)
        updated, revision = self._save(project_id, updated, revision)
        self._step(job_id, "global_alignment", 1.0, "stable-ts 全曲行级对齐完成")
        return updated, revision, summary | {"output_revision": revision}

    def _align(self, job_id: str, project_id: str, document: dict, revision: int, payload: dict, values: dict) -> tuple[dict, int, dict]:
        _, derived, _ = self._paths(project_id)
        audio = derived / "vocals_asr.wav"
        if not audio.is_file():
            raise AudioProcessingError("请先完成人声分离")
        model_name = payload.get("whisper_model") or payload.get("model") or values.get("whisper_model") or self.settings.whisper_model
        stable = self._stable_aligner(job_id, "alignment", model_name, values)
        line_ids = payload.get("line_ids") or None
        segment_padding = float(values.get("stable_ts_segment_padding_seconds", 2.0))
        clip_path, time_offset = self._single_line_clip(job_id, document, derived, audio, line_ids)
        try:
            updated, summary = stable.align_words(
                document,
                clip_path or audio,
                line_ids=line_ids,
                overwrite_locked=payload.get("overwrite_policy") == "all",
                segment_padding_seconds=0.0 if clip_path else segment_padding,
                time_offset_ms=time_offset,
                progress_callback=lambda value, message: self._step(job_id, "alignment", value, message),
            )
        finally:
            if clip_path:
                clip_path.unlink(missing_ok=True)
        updated.setdefault("analysis", {})["alignment"] = {"status": "completed", "job_id": job_id, **summary}
        updated, revision = self._save(project_id, updated, revision)
        self._step(job_id, "alignment", 1.0, "stable-ts 词/短语精修完成")
        return updated, revision, summary | {"output_revision": revision}

    def _fa_kara(self, job_id: str, project_id: str, document: dict, revision: int, payload: dict, values: dict) -> tuple[dict, int, dict]:
        _, derived, _ = self._paths(project_id)
        audio = derived / "vocals_asr.wav"
        if not audio.is_file():
            raise AudioProcessingError("请先完成人声分离，再运行 FA-Kara 对齐")
        model_name = str(payload.get("fa_kara_model") or payload.get("model") or values.get("fa_kara_model") or "mms")
        aligner = FAKaraAligner(
            lambda: self.transcriber.get_fa_kara_model(
                model_name=model_name,
                device=values.get("whisper_device") or self.settings.whisper_device,
                progress_callback=lambda value, message: self._step(job_id, "fa_kara", 0.02 + value * 0.08, message),
                should_cancel=lambda: self._should_cancel(job_id),
            ),
            model_name=model_name,
        )
        result_path = derived / "fa_kara.json"
        line_ids = payload.get("line_ids") or None
        clip_path, time_offset = self._single_line_clip(job_id, document, derived, audio, line_ids)
        try:
            updated, summary = aligner.align(
                document,
                clip_path or audio,
                line_ids=line_ids,
                overwrite_locked=payload.get("overwrite_policy") == "all",
                result_path=result_path,
                time_offset_ms=time_offset,
                progress_callback=lambda value, message: self._step(job_id, "fa_kara", 0.1 + value * 0.88, message),
            )
        finally:
            if clip_path:
                clip_path.unlink(missing_ok=True)
        updated.setdefault("analysis", {})["fa_kara"] = {"status": "completed", "job_id": job_id, **summary}
        updated, revision = self._save(project_id, updated, revision)
        self._step(job_id, "fa_kara", 1.0, "FA-Kara 对齐完成")
        return updated, revision, summary | {"output_revision": revision}

    def _single_line_clip(
        self,
        job_id: str,
        document: dict,
        derived: Path,
        audio: Path,
        line_ids: list[str] | None,
    ) -> tuple[Path | None, int]:
        if not line_ids or len(line_ids) != 1:
            return None, 0
        line = next((item for item in document.get("lyrics", {}).get("lines", []) if item.get("id") == line_ids[0]), None)
        if not line:
            raise AudioProcessingError("局部识别范围包含未知歌词行")
        start_ms, end_ms = line.get("start_ms"), line.get("end_ms")
        if start_ms is None or end_ms is None or int(end_ms) <= int(start_ms):
            raise AudioProcessingError("请先调整整句歌词的开始和结束时间")
        start_ms, end_ms = int(start_ms), int(end_ms)
        clip_path = derived / f"line_alignment_{job_id}.wav"
        self._step(job_id, "alignment" if "STABLE" in str(self.database.get_job(job_id).get("type", "")) else "fa_kara", 0.01, "正在截取单句人声音频")
        extract_audio_clip(audio, clip_path, self.settings.ffmpeg_path, start_ms, end_ms)
        return clip_path, start_ms

    def _prepare_export_instrumental(self, job_id: str, project_id: str, payload: dict, values: dict) -> Path:
        video, derived, _ = self._paths(project_id)
        if not video.is_file():
            raise AudioProcessingError("工程尚未上传视频")
        legacy_model = values.get("separator_model") or self.settings.separator_model
        model = payload.get("separator_instrumental_model") or values.get("separator_instrumental_model") or legacy_model
        device = payload.get("separator_device") or values.get("separator_device") or self.settings.separator_device
        self._step(job_id, "export", 0.01, "正在检查 OFF VOCAL 音频缓存")
        source_audio_path = derived / "source_audio.wav"
        if source_audio_path.is_file() and source_audio_path.stat().st_mtime_ns < video.stat().st_mtime_ns:
            source_audio_path.unlink(missing_ok=True)
            (derived / "source_asr.wav").unlink(missing_ok=True)
        if not source_audio_path.is_file() or source_audio_path.stat().st_size == 0:
            convert_audio(video, source_audio_path, self.settings.ffmpeg_path, channels=2, sample_rate=44100)
        source_audio = source_audio_path
        instrumental = derived / "instrumental.wav"
        cache_path = derived / "instrumental.meta.json"
        cache_key = {
            "version": 1,
            "model": str(model),
            "video_size": video.stat().st_size,
            "video_mtime_ns": video.stat().st_mtime_ns,
            "source_size": source_audio.stat().st_size,
            "source_mtime_ns": source_audio.stat().st_mtime_ns,
        }
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else None
        except (OSError, ValueError):
            cached = None
        if instrumental.is_file() and instrumental.stat().st_size > 0 and cached == cache_key:
            self._step(job_id, "export", 0.2, f"正在复用 OFF VOCAL 分离缓存：{model}")
            return instrumental

        self._step(job_id, "export", 0.02, f"正在使用 {model} 生成 OFF VOCAL 音频")
        token = job_id.replace("-", "")[:12]
        staged_vocals_name = f"{token}_unused_export_vocals.wav"
        staged_instrumental_name = f"{token}_export_instrumental.wav"
        staged_cache_path = derived / f"{token}_instrumental.meta.json"
        staged_paths = [derived / staged_vocals_name, derived / staged_instrumental_name, staged_cache_path]
        try:
            _, staged_instrumental, _ = self.separator.separate(
                source_audio,
                derived,
                model=str(model),
                device=str(device),
                progress_callback=lambda value, message: self._step(job_id, "export", 0.02 + value * 0.18, message),
                should_cancel=lambda: self._should_cancel(job_id),
                vocals_filename=staged_vocals_name,
                instrumental_filename=staged_instrumental_name,
                asr_filename=None,
            )
            staged_cache_path.write_text(json.dumps(cache_key, ensure_ascii=False, indent=2), encoding="utf-8")
            staged_instrumental.replace(instrumental)
            staged_cache_path.replace(cache_path)
            return instrumental
        finally:
            for staged_path in staged_paths:
                staged_path.unlink(missing_ok=True)

    def _export(self, job_id: str, project_id: str, document: dict, payload: dict, values: dict) -> dict:
        off_vocal = payload.get("format") in {"mp4", "webm"} and payload.get("audio_track") == "off_vocal"
        if off_vocal:
            self._prepare_export_instrumental(job_id, project_id, payload, values)
        return run_kirakara_export(
            job_id,
            project_id,
            document,
            payload,
            self.settings,
            progress_callback=lambda value, message: self._step(job_id, "export", 0.2 + value * 0.8 if off_vocal else value, message),
            should_cancel=lambda: bool(self.database.get_job(job_id) and self.database.get_job(job_id)["cancel_requested"]),
        )

    def process(self, job_id: str) -> dict:
        job = self.database.get_job(job_id)
        if not job:
            raise ValueError("job_not_found")
        values = self._settings()
        project_id, payload = job["project_id"], job["request"]
        document = self.database.document(project_id, job["input_revision"])
        if not document:
            raise ValueError("input_revision_not_found")
        revision = job["input_revision"]
        if job["type"] == "VOCAL_SEPARATION":
            return self._separate(job_id, project_id, document, revision, payload, values)[2]
        if job["type"] == "TRANSCRIPTION":
            return self._transcribe(job_id, project_id, document, revision, payload, values)[2]
        if job["type"] == "PRONUNCIATION":
            result: dict = {}
            for step in payload.get("steps", ["pronunciation"]):
                if step == "separation":
                    document, revision, step_result = self._separate(job_id, project_id, document, revision, payload, values)
                elif step == "transcription":
                    document, revision, step_result = self._transcribe(job_id, project_id, document, revision, payload, values)
                elif step == "pronunciation":
                    document, revision, step_result = self._pronounce(job_id, project_id, document, revision, payload)
                else:
                    raise ValueError("unknown_pronunciation_step")
                result[step] = step_result
            result["output_revision"] = revision
            return result
        if job["type"] == "STABLE_GLOBAL_ALIGNMENT":
            return self._global_align(job_id, project_id, document, revision, payload, values)[2]
        if job["type"] == "STABLE_ALIGNMENT":
            return self._align(job_id, project_id, document, revision, payload, values)[2]
        if job["type"] == "FA_KARA_ALIGNMENT":
            return self._fa_kara(job_id, project_id, document, revision, payload, values)[2]
        if job["type"] == "EXPORT":
            return self._export(job_id, project_id, document, payload, values)
        if job["type"] != "FULL_ANALYSIS":
            raise ValueError("unknown_job_type")
        result: dict = {}
        default_steps = ["separation", "transcription", "pronunciation", "fa_kara"]
        for step in payload.get("steps", default_steps):
            if step in {"global_alignment", "alignment", "fa_kara"}:
                self._require_alignment_ruby(document)
            if step == "separation":
                document, revision, step_result = self._separate(job_id, project_id, document, revision, payload, values)
            elif step == "transcription":
                document, revision, step_result = self._transcribe(job_id, project_id, document, revision, payload, values)
            elif step == "pronunciation":
                document, revision, step_result = self._pronounce(job_id, project_id, document, revision, payload)
            elif step == "global_alignment":
                document, revision, step_result = self._global_align(job_id, project_id, document, revision, payload, values)
            elif step == "alignment":
                document, revision, step_result = self._align(job_id, project_id, document, revision, payload, values)
            elif step == "fa_kara":
                document, revision, step_result = self._fa_kara(job_id, project_id, document, revision, payload, values)
            else:
                raise ValueError("unknown_analysis_step")
            result[step] = step_result
        result["output_revision"] = revision
        return result


class AnalysisRunner:
    def __init__(self, database: Database, pipeline: AnalysisPipeline, max_workers: int = 1) -> None:
        self.database, self.pipeline = database, pipeline
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nicokara-analysis")

    def enqueue(self, project_id: str, job_type: str, input_revision: int, payload: dict) -> dict:
        job = self.database.create_job(str(uuid4()), project_id, job_type, input_revision, payload)
        self.executor.submit(self._run, job["id"])
        return job

    def _run(self, job_id: str) -> None:
        self.database.update_job(job_id, status="PREPARING", started_at=timestamp(), message="任务已开始")
        try:
            result = self.pipeline.process(job_id)
            self.database.update_job(job_id, status="SUCCEEDED", progress=1.0, stage="COMPLETED", message="处理完成", result_json=result, output_revision=result.get("output_revision"), completed_at=timestamp())
        except (JobCanceled, OperationCanceled):
            self.database.update_job(job_id, status="CANCELED", message="任务已取消", completed_at=timestamp())
        except ExportCanceled:
            self.database.update_job(job_id, status="CANCELED", message="导出已取消", completed_at=timestamp())
        except MissingRubyError as exc:
            self.database.update_job(job_id, status="FAILED", error_code="missing_ruby", error_message=str(exc), message="等待选择注音方式", completed_at=timestamp())
        except (AudioProcessingError, AlignmentQualityError, PronunciationValidationError, ExportError) as exc:
            self.database.update_job(job_id, status="FAILED", error_code="analysis_failed", error_message=str(exc), message="处理失败", completed_at=timestamp())
        except RuntimeError as exc:
            code = "revision_conflict" if str(exc) == "revision_conflict" else "runtime_error"
            message = "工程已被编辑，分析结果未覆盖当前版本" if code == "revision_conflict" else str(exc)
            self.database.update_job(job_id, status="FAILED", error_code=code, error_message=message, message="处理失败", completed_at=timestamp())
        except Exception:
            logger.exception("Unhandled analysis job failure (job_id=%s)", job_id)
            self.database.update_job(job_id, status="FAILED", error_code="internal_error", error_message="分析任务发生内部错误，请查看后端日志", message="处理失败", completed_at=timestamp())

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
