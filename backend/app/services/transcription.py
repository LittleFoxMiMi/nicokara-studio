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
        self._stable_models: dict[tuple[str, str, str], Any] = {}
        self._fa_kara_models: dict[tuple[str, str], tuple[Any, Any]] = {}

    def get_model(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Any:
        resolved_device = "cpu"
        resolved_compute = "int8"
        key = (model_name, resolved_device, resolved_compute)
        model = self._models.get(key)
        if model is None:
            if progress_callback:
                progress_callback(0.0, "正在下载或加载 Whisper 模型")
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
            if progress_callback:
                progress_callback(1.0, "Whisper 模型已就绪")
        return model

    def transcribe(
        self,
        audio: Path,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Transcript:
        model = self.get_model(
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            progress_callback=progress_callback,
        )
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
            if progress_callback:
                duration = max(0.001, float(info.duration))
                progress_callback(min(1.0, max(0.0, float(raw.end) / duration)), "Whisper 正在粗识别")
        if progress_callback:
            progress_callback(1.0, "Whisper 粗识别完成")
        return Transcript(str(info.language), float(info.language_probability), float(info.duration), segments)

    def get_stable_model(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Any:
        """Load stable-ts's official faster-whisper model integration."""
        resolved_device = "cpu"
        resolved_compute = "int8"
        key = (model_name, resolved_device, resolved_compute)
        model = self._stable_models.get(key)
        if model is not None:
            return model
        if progress_callback:
            progress_callback(0.0, "正在加载 stable-ts faster-whisper 模型")
        try:
            import stable_whisper
        except ImportError as exc:
            raise RuntimeError("stable-ts 尚未安装") from exc
        if self.download_root:
            self.download_root.mkdir(parents=True, exist_ok=True)
        model = stable_whisper.load_faster_whisper(
            model_name,
            device=resolved_device,
            compute_type=resolved_compute,
            download_root=str(self.download_root) if self.download_root else None,
        )
        self._stable_models[key] = model
        if progress_callback:
            progress_callback(1.0, "stable-ts faster-whisper 模型已就绪")
        return model

    def get_fa_kara_model(
        self,
        *,
        model_name: str = "mms",
        device: str = "cpu",
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[Any, Any]:
        """Load either FA-Kara CTC backend without pronunciation fallbacks."""
        if model_name not in {"mms", "yohane"}:
            raise RuntimeError(f"不支持的 FA-Kara 模型：{model_name}")
        try:
            import torch
            import torchaudio
        except ImportError as exc:
            raise RuntimeError("FA-Kara 需要 torch 和 torchaudio") from exc
        resolved_device = "cuda" if device in {"auto", "cuda"} and torch.cuda.is_available() else "cpu"
        key = (model_name, resolved_device)
        cached = self._fa_kara_models.get(key)
        if cached is not None:
            return cached
        if model_name == "mms":
            if progress_callback:
                progress_callback(0.0, "正在下载或加载 FA-Kara MMS_FA 模型")
            bundle = torchaudio.pipelines.MMS_FA
            model = bundle.get_model().to(resolved_device)
            ready_message = "FA-Kara MMS_FA 模型已就绪"
        else:
            if progress_callback:
                progress_callback(0.0, "正在下载或加载 FA-Kara YoHane 微调模型")
            try:
                from torchaudio.functional import merge_tokens
                from torchaudio.pipelines._wav2vec2 import aligner as emission_aligner
                from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
            except ImportError as exc:
                raise RuntimeError("FA-Kara YoHane 模型需要 transformers") from exc

            model_id = "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
            cache_dir = self.download_root.parent / "fa-kara" if self.download_root else None
            if cache_dir:
                cache_dir.mkdir(parents=True, exist_ok=True)
            processor = Wav2Vec2Processor.from_pretrained(model_id, cache_dir=str(cache_dir) if cache_dir else None)
            hf_model = Wav2Vec2ForCTC.from_pretrained(model_id, cache_dir=str(cache_dir) if cache_dir else None)
            blank = hf_model.config.pad_token_id
            if blank is None:
                raise RuntimeError("YoHane 模型没有配置 CTC blank token")

            class YoHaneBundle:
                sample_rate = int(processor.feature_extractor.sampling_rate)
                frame_hop_samples = int(getattr(hf_model.config, "inputs_to_logits_ratio", 320))

                @staticmethod
                def get_tokenizer():
                    return lambda batch: [processor.tokenizer.encode(text, add_special_tokens=False) for text in batch]

                @staticmethod
                def get_aligner():
                    def align(emission, token_groups):
                        flat_tokens = [token for group in token_groups for token in group]
                        aligned_tokens, scores = emission_aligner._align_emission_and_tokens(emission, flat_tokens, blank=blank)
                        spans = merge_tokens(aligned_tokens, scores, blank=blank)
                        expected = sum(len(group) for group in token_groups)
                        if len(spans) != expected:
                            raise RuntimeError("YoHane 对齐结果与输入 token 数量不一致")
                        grouped = []
                        offset = 0
                        for group in token_groups:
                            grouped.append(spans[offset:offset + len(group)])
                            offset += len(group)
                        return grouped

                    return align

            bundle = YoHaneBundle()

            class YoHaneModel(torch.nn.Module):
                def __init__(self) -> None:
                    super().__init__()
                    self.model = hf_model

                def forward(self, waveform):
                    samples = waveform.mean(0).detach().cpu().numpy()
                    inputs = processor(samples, sampling_rate=bundle.sample_rate, return_tensors="pt")
                    target_device = next(self.model.parameters()).device
                    prepared = {name: value.to(target_device) for name, value in inputs.items()}
                    outputs = self.model(**prepared)
                    return torch.nn.functional.log_softmax(outputs.logits, dim=-1), None

            model = YoHaneModel().to(resolved_device).eval()
            ready_message = "FA-Kara YoHane 微调模型已就绪"
        self._fa_kara_models[key] = (model, bundle)
        if progress_callback:
            progress_callback(1.0, ready_message)
        return model, bundle
