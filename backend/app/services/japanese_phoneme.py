from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from app.services.cancellation import OperationCanceled, run_cancelable
from app.services.model_runtime import ResidentModelStore


MODEL_NAME = "prj-beatrice/japanese-hubert-base-phoneme-ctc-v4"
SAMPLE_RATE = 16_000
CHUNK_SECONDS = 20


class JapanesePhonemeError(RuntimeError):
    pass


@dataclass(frozen=True)
class PhonemeToken:
    phone: str
    start_ms: int
    end_ms: int


@contextmanager
def _proxy_environment(proxy_url: str | None) -> Iterator[None]:
    if not proxy_url:
        yield
        return
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = proxy_url
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def split_phonemes_at_segment_starts(transcript: dict[str, Any], tokens: list[PhonemeToken]) -> dict[str, Any]:
    """Attach CTC phones using only consecutive Whisper start boundaries."""
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise JapanesePhonemeError("Whisper 粗识别结果缺少 segments")
    ordered_tokens = sorted(tokens, key=lambda item: (item.start_ms, item.end_ms))
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise JapanesePhonemeError("Whisper 粗识别 segment 格式无效")
        start_ms = int(segment.get("start_ms", 0))
        next_start_ms = None
        if index + 1 < len(segments):
            following = segments[index + 1]
            if not isinstance(following, dict):
                raise JapanesePhonemeError("Whisper 粗识别 segment 格式无效")
            next_start_ms = int(following.get("start_ms", start_ms))
        phones = [
            token.phone
            for token in ordered_tokens
            if token.start_ms >= start_ms and (next_start_ms is None or token.start_ms < next_start_ms)
        ]
        segment["phonemes"] = " ".join(phones)
    transcript["phoneme_model"] = MODEL_NAME
    transcript["phoneme_segmentation"] = "whisper_segment_start_boundaries"
    return transcript


class JapanesePhonemeRecognizer:
    def __init__(
        self,
        *,
        cache_dir: Path,
        model_store: ResidentModelStore | None = None,
        model_factory: Callable[[], tuple[Any, Any]] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.model_store = model_store or ResidentModelStore()
        self.model_factory = model_factory

    def get_model(
        self,
        *,
        proxy_url: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[Any, Any]:
        def load_model() -> tuple[Any, Any]:
            if self.model_factory:
                return run_cancelable(self.model_factory, should_cancel)
            try:
                from transformers import HubertForCTC, Wav2Vec2Processor
            except ImportError as exc:
                raise JapanesePhonemeError("日语音素识别需要 transformers") from exc
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with _proxy_environment(proxy_url):
                processor = run_cancelable(
                    lambda: Wav2Vec2Processor.from_pretrained(MODEL_NAME, cache_dir=str(self.cache_dir)),
                    should_cancel,
                )
                model = run_cancelable(
                    lambda: HubertForCTC.from_pretrained(MODEL_NAME, cache_dir=str(self.cache_dir)).eval(),
                    should_cancel,
                )
            return model, processor

        if progress_callback:
            progress_callback(0.0, "正在下载或加载日语 HuBERT 音素模型")
        model = self.model_store.get_or_load(
            f"japanese-phoneme:{MODEL_NAME}:cpu",
            "Japanese HuBERT phoneme CTC",
            load_model,
        )
        if progress_callback:
            progress_callback(1.0, "日语 HuBERT 音素模型已就绪")
        return model

    @staticmethod
    def _decode_chunk(ids: Any, processor: Any, start_ms: int, duration_ms: int) -> list[PhonemeToken]:
        values = ids.tolist()
        tokens = processor.tokenizer.convert_ids_to_tokens(values)
        special_ids = set(getattr(processor.tokenizer, "all_special_ids", []))
        special_tokens = set(getattr(processor.tokenizer, "all_special_tokens", []))
        frame_count = max(1, len(values))
        decoded: list[PhonemeToken] = []
        cursor = 0
        while cursor < len(values):
            end = cursor + 1
            while end < len(values) and values[end] == values[cursor]:
                end += 1
            token = str(tokens[cursor])
            if values[cursor] not in special_ids and token not in special_tokens:
                decoded.append(
                    PhonemeToken(
                        phone=token,
                        start_ms=round(start_ms + duration_ms * cursor / frame_count),
                        end_ms=round(start_ms + duration_ms * end / frame_count),
                    )
                )
            cursor = end
        return decoded

    def recognize(
        self,
        audio: Path,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        proxy_url: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[PhonemeToken]:
        try:
            import soundfile as sf
            import torch
        except ImportError as exc:
            raise JapanesePhonemeError("日语音素识别需要 torch 和 soundfile") from exc
        if not audio.is_file():
            raise JapanesePhonemeError(f"音素识别音频不存在：{audio}")
        try:
            info = sf.info(str(audio))
        except Exception as exc:
            raise JapanesePhonemeError("无法读取日语音素识别音频") from exc
        if int(info.samplerate) != SAMPLE_RATE:
            raise JapanesePhonemeError(f"日语音素识别需要 {SAMPLE_RATE} Hz 音频，当前为 {info.samplerate} Hz")

        model, processor = self.get_model(
            proxy_url=proxy_url,
            progress_callback=None,
            should_cancel=should_cancel,
        )
        first_frame = max(0, round((start_ms or 0) * SAMPLE_RATE / 1000))
        last_frame = min(int(info.frames), round(end_ms * SAMPLE_RATE / 1000)) if end_ms is not None else int(info.frames)
        if last_frame <= first_frame:
            return []
        chunk_frames = SAMPLE_RATE * CHUNK_SECONDS
        total_frames = last_frame - first_frame
        result: list[PhonemeToken] = []
        for offset in range(first_frame, last_frame, chunk_frames):
            if should_cancel and should_cancel():
                raise OperationCanceled()
            stop = min(last_frame, offset + chunk_frames)
            waveform, sample_rate = sf.read(
                str(audio),
                start=offset,
                stop=stop,
                dtype="float32",
                always_2d=False,
            )
            if int(sample_rate) != SAMPLE_RATE:
                raise JapanesePhonemeError("日语音素识别期间音频采样率发生变化")
            if getattr(waveform, "ndim", 1) != 1:
                waveform = waveform.mean(axis=1)
            inputs = processor(waveform, sampling_rate=SAMPLE_RATE, return_tensors="pt")

            def infer() -> Any:
                with torch.inference_mode():
                    return model(**inputs).logits.argmax(dim=-1)[0]

            ids = run_cancelable(infer, should_cancel)
            chunk_start_ms = round(offset * 1000 / SAMPLE_RATE)
            chunk_duration_ms = round((stop - offset) * 1000 / SAMPLE_RATE)
            result.extend(self._decode_chunk(ids, processor, chunk_start_ms, chunk_duration_ms))
            if progress_callback:
                progress_callback((stop - first_frame) / total_frames, "日语 HuBERT 正在识别拉丁音素")
        if progress_callback:
            progress_callback(1.0, "日语 HuBERT 音素识别完成")
        return result


def phoneme_result(tokens: list[PhonemeToken]) -> dict[str, Any]:
    return {
        "model": MODEL_NAME,
        "sample_rate": SAMPLE_RATE,
        "segmentation": "Whisper segment start boundaries; end_ms is intentionally ignored.",
        "tokens": [asdict(token) for token in tokens],
    }
