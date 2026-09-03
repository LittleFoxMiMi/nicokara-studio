from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from app.services.audio import AudioProcessingError, convert_audio
from app.services.cancellation import OperationCanceled, run_cancelable
from app.services.model_runtime import ResidentModelStore

DEFAULT_MODEL = "UVR_MDXNET_KARA_2.onnx"

# audio-separator's public MDX/VR models that produce a vocals/instrumental
# pair. Models targeting drums, noise, reverb, and other stems are excluded
# because the rest of the pipeline requires these two exact stems.
SEPARATOR_MODEL_GROUPS = {
    "MDX": [
        ("UVR-MDX-NET Karaoke 2", "UVR_MDXNET_KARA_2.onnx"),
        ("UVR-MDX-NET Karaoke", "UVR_MDXNET_KARA.onnx"),
        ("Kim Vocal 2", "Kim_Vocal_2.onnx"),
        ("Kim Vocal 1", "Kim_Vocal_1.onnx"),
        ("UVR-MDX-NET Voc FT", "UVR-MDX-NET-Voc_FT.onnx"),
        ("UVR-MDX-NET Main", "UVR_MDXNET_Main.onnx"),
        ("UVR-MDX-NET 1", "UVR_MDXNET_1_9703.onnx"),
        ("UVR-MDX-NET 2", "UVR_MDXNET_2_9682.onnx"),
        ("UVR-MDX-NET 3", "UVR_MDXNET_3_9662.onnx"),
        ("UVR-MDX-NET 9482", "UVR_MDXNET_9482.onnx"),
        ("kuielab A Vocals", "kuielab_a_vocals.onnx"),
        ("kuielab B Vocals", "kuielab_b_vocals.onnx"),
        ("UVR-MDX-NET Inst HQ 1", "UVR-MDX-NET-Inst_HQ_1.onnx"),
        ("UVR-MDX-NET Inst HQ 2", "UVR-MDX-NET-Inst_HQ_2.onnx"),
        ("UVR-MDX-NET Inst HQ 3", "UVR-MDX-NET-Inst_HQ_3.onnx"),
        ("UVR-MDX-NET Inst HQ 4", "UVR-MDX-NET-Inst_HQ_4.onnx"),
        ("UVR-MDX-NET Inst HQ 5", "UVR-MDX-NET-Inst_HQ_5.onnx"),
        ("UVR-MDX-NET Inst Main", "UVR-MDX-NET-Inst_Main.onnx"),
        ("UVR-MDX-NET Inst 1", "UVR-MDX-NET-Inst_1.onnx"),
        ("UVR-MDX-NET Inst 2", "UVR-MDX-NET-Inst_2.onnx"),
        ("UVR-MDX-NET Inst 3", "UVR-MDX-NET-Inst_3.onnx"),
        ("Kim Inst", "Kim_Inst.onnx"),
    ],
    "VR": [
        ("1 HP", "1_HP-UVR.pth"),
        ("2 HP", "2_HP-UVR.pth"),
        ("3 HP Vocal", "3_HP-Vocal-UVR.pth"),
        ("4 HP Vocal", "4_HP-Vocal-UVR.pth"),
        ("5 HP Karaoke", "5_HP-Karaoke-UVR.pth"),
        ("6 HP Karaoke", "6_HP-Karaoke-UVR.pth"),
        ("7 HP2", "7_HP2-UVR.pth"),
        ("8 HP2", "8_HP2-UVR.pth"),
        ("9 HP2", "9_HP2-UVR.pth"),
        ("10 SP 2B 32000-1", "10_SP-UVR-2B-32000-1.pth"),
        ("11 SP 2B 32000-2", "11_SP-UVR-2B-32000-2.pth"),
        ("12 SP 3B 44100", "12_SP-UVR-3B-44100.pth"),
        ("13 SP 4B 44100-1", "13_SP-UVR-4B-44100-1.pth"),
        ("14 SP 4B 44100-2", "14_SP-UVR-4B-44100-2.pth"),
        ("15 SP MID 44100-1", "15_SP-UVR-MID-44100-1.pth"),
        ("16 SP MID 44100-2", "16_SP-UVR-MID-44100-2.pth"),
        ("BVE 4B SN 44100-1", "UVR-BVE-4B_SN-44100-1.pth"),
        ("BVE 4B SN 44100-2", "UVR-BVE-4B_SN-44100-2.pth"),
        ("MGM High End v4", "MGM_HIGHEND_v4.pth"),
        ("MGM Low End A v4", "MGM_LOWEND_A_v4.pth"),
        ("MGM Low End B v4", "MGM_LOWEND_B_v4.pth"),
        ("MGM Main v4", "MGM_MAIN_v4.pth"),
    ],
}

SUPPORTED_SEPARATOR_MODELS = {
    filename for models in SEPARATOR_MODEL_GROUPS.values() for _, filename in models
}


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


class VocalSeparator:
    def __init__(
        self,
        model_dir: Path,
        ffmpeg: str,
        *,
        separator_factory: Callable[..., Any] | None = None,
        providers_factory: Callable[[], list[str]] | None = None,
        model_store: ResidentModelStore | None = None,
    ) -> None:
        self.model_dir = model_dir
        self.ffmpeg = ffmpeg
        self.separator_factory = separator_factory
        self.providers_factory = providers_factory
        self.model_store = model_store or ResidentModelStore()
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
        vocals_filename: str = "vocals.wav",
        instrumental_filename: str = "instrumental.wav",
        asr_filename: str | None = "vocals_asr.wav",
    ) -> tuple[Path, Path, Path | None]:
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
        vocals = derived_dir / vocals_filename
        instrumental = derived_dir / instrumental_filename
        asr = derived_dir / asr_filename if asr_filename else None
        for path in (vocals, instrumental, asr):
            if path is None:
                continue
            path.unlink(missing_ok=True)
        try:
            with self._lock:
                def load_separator() -> Any:
                    loaded = factory(
                        model_file_dir=str(self.model_dir),
                        output_dir=str(derived_dir),
                        output_format="WAV",
                        use_directml=provider == "DmlExecutionProvider",
                        mdx_params={
                            "hop_length": 1024,
                            "segment_size": 256,
                            "overlap": 0.25,
                            "batch_size": 1,
                            "enable_denoise": True,
                        },
                    )
                    # audio-separator 0.44 requires torch-directml before it selects
                    # the ONNX DML provider, although MDX inference itself is ONNX.
                    if provider == "DmlExecutionProvider":
                        loaded.onnx_execution_provider = ["DmlExecutionProvider"]
                    run_cancelable(lambda: loaded.load_model(model_filename=model), should_cancel)
                    return loaded

                runtime_key = f"separator:{model}:{provider}:{derived_dir.resolve()}"
                separator = self.model_store.get_or_load(runtime_key, f"人声分离 {model}", load_separator)
                if progress_callback:
                    progress_callback(0.42, "分离模型已就绪，正在处理音频")
                outputs = separator.separate(
                    str(source),
                    {"Vocals": vocals.stem, "Instrumental": instrumental.stem},
                )
                self._place_outputs(outputs, derived_dir, vocals, instrumental)
                if progress_callback:
                    progress_callback(0.82, "vocals / instrumental 已生成")
        except (AudioProcessingError, OperationCanceled):
            raise
        except Exception as exc:
            raise AudioProcessingError("人声分离失败；请检查模型、设备和可用内存") from exc
        if asr is not None:
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
            raise AudioProcessingError(f"分离模型未生成 {target.name}")
