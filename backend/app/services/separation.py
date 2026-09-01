from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from app.services.audio import AudioProcessingError, convert_audio
from app.services.cancellation import OperationCanceled, run_cancelable

DEFAULT_MODEL = "UVR_MDXNET_KARA_2.onnx"


def provider_for(device: str, available: list[str]) -> str:
    choices = {
        "directml": "DmlExecutionProvider",
        "cpu": "CPUExecutionProvider",
    }
    if device == "auto":
        for provider in ("DmlExecutionProvider", "CPUExecutionProvider"):
            if provider in available:
                return provider
        return "CPUExecutionProvider"
    provider = choices[device]
    if provider not in available:
        raise AudioProcessingError(f"所选分离设备不可用：{device}")
    return provider


class Kara2Separator:
    def __init__(
        self,
        model_dir: Path,
        ffmpeg: str,
        *,
        separator_factory: Callable[..., Any] | None = None,
        providers_factory: Callable[[], list[str]] | None = None,
    ) -> None:
        self.model_dir = model_dir
        self.ffmpeg = ffmpeg
        self.separator_factory = separator_factory
        self.providers_factory = providers_factory
        self._lock = threading.Lock()

    def _available_providers(self) -> list[str]:
        if self.providers_factory:
            return self.providers_factory()
        try:
            import onnxruntime as ort
            return list(ort.get_available_providers())
        except ImportError:
            return []

    def separate(
        self,
        source: Path,
        derived_dir: Path,
        *,
        model: str = DEFAULT_MODEL,
        device: str = "auto",
        progress_callback: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[Path, Path, Path]:
        factory = self.separator_factory
        if factory is None:
            try:
                from audio_separator.separator import Separator
            except ImportError as exc:
                raise AudioProcessingError("audio-separator 尚未安装") from exc
            factory = Separator
        provider = provider_for(device, self._available_providers())
        self.model_dir.mkdir(parents=True, exist_ok=True)
        derived_dir.mkdir(parents=True, exist_ok=True)
        vocals = derived_dir / "vocals.wav"
        instrumental = derived_dir / "instrumental.wav"
        asr = derived_dir / "vocals_asr.wav"
        for path in (vocals, instrumental, asr):
            path.unlink(missing_ok=True)
        try:
            with self._lock:
                separator = factory(
                    model_file_dir=str(self.model_dir),
                    output_dir=str(derived_dir),
                    output_format="WAV",
                    use_directml=provider == "DmlExecutionProvider",
                )
                # audio-separator 0.44 requires torch-directml before it selects the
                # ONNX DML provider, although MDX/KARA2 inference itself is ONNX.
                if provider == "DmlExecutionProvider":
                    separator.onnx_execution_provider = ["DmlExecutionProvider"]
                run_cancelable(
                    lambda: separator.load_model(model_filename=model),
                    should_cancel,
                )
                if progress_callback:
                    progress_callback(0.42, "KARA2 模型已就绪，正在分离人声")
                outputs = separator.separate(
                    str(source),
                    {"Vocals": "vocals", "Instrumental": "instrumental"},
                )
                self._place_outputs(outputs, derived_dir, vocals, instrumental)
                if progress_callback:
                    progress_callback(0.82, "KARA2 双 stem 已生成")
        except (AudioProcessingError, OperationCanceled):
            raise
        except Exception as exc:
            raise AudioProcessingError("KARA2 分离失败；请检查模型、设备和可用内存") from exc
        convert_audio(vocals, asr, self.ffmpeg, channels=1, sample_rate=16000)
        if progress_callback:
            progress_callback(0.96, "正在生成 Whisper 16 kHz 人声音频")
        return vocals, instrumental, asr

    @staticmethod
    def _place_outputs(outputs: list[str], directory: Path, vocals: Path, instrumental: Path) -> None:
        candidates: list[Path] = []
        for name in outputs:
            candidate = Path(name)
            candidates.append(candidate if candidate.is_absolute() else directory / candidate)
        for target, keyword in ((vocals, "vocal"), (instrumental, "instrument")):
            if target.is_file() and target.stat().st_size > 0:
                continue
            candidate = next((item for item in candidates if item.is_file() and keyword in item.stem.lower() and item.stat().st_size > 0), None)
            if candidate:
                if candidate.resolve() != target.resolve():
                    shutil.move(str(candidate), str(target))
                continue
            raise AudioProcessingError(f"KARA2 未生成 {target.name}")
