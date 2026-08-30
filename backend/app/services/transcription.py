from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start_ms: int
    end_ms: int
    confidence: float


@dataclass(frozen=True)
class TranscriptSegment:
    id: int
    text: str
    start_ms: int
    end_ms: int
    confidence: float
    no_speech_probability: float
    words: list[TranscriptWord] = field(default_factory=list)


@dataclass(frozen=True)
class Transcript:
    language: str
    language_probability: float
    duration_seconds: float
    segments: list[TranscriptSegment]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FasterWhisperTranscriber:
    def __init__(self, model_factory: Callable[[str, str, str], Any] | None = None, download_root: Path | None = None) -> None:
        self.model_factory = model_factory
        self.download_root = download_root
        self._models: dict[tuple[str, str, str], Any] = {}

    def transcribe(self, audio: Path, *, model_name: str, device: str, compute_type: str, start_ms: int | None = None, end_ms: int | None = None) -> Transcript:
        resolved_device = "cpu"
        resolved_compute = "int8"
        key = (model_name, resolved_device, resolved_compute)
        model = self._models.get(key)
        if model is None:
            if self.model_factory:
                model = self.model_factory(*key)
            else:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise RuntimeError("faster-whisper 尚未安装") from exc
                if self.download_root:
                    self.download_root.mkdir(parents=True, exist_ok=True)
                model = WhisperModel(
                    model_name,
                    device=resolved_device,
                    compute_type=resolved_compute,
                    download_root=str(self.download_root) if self.download_root else None,
                )
            self._models[key] = model
        options: dict[str, Any] = {
            "language": "ja",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
            "condition_on_previous_text": False,
        }
        if start_ms is not None and end_ms is not None:
            options["clip_timestamps"] = [start_ms / 1000, end_ms / 1000]
        raw_segments, info = model.transcribe(str(audio), **options)
        segments = []
        for raw in raw_segments:
            words = [
                TranscriptWord(word.word.strip(), round(word.start * 1000), round(word.end * 1000), float(word.probability))
                for word in (raw.words or []) if word.word.strip()
            ]
            segments.append(
                TranscriptSegment(int(raw.id), raw.text.strip(), round(raw.start * 1000), round(raw.end * 1000), float(raw.avg_logprob), float(raw.no_speech_prob), words)
            )
        return Transcript(str(info.language), float(info.language_probability), float(info.duration), segments)
