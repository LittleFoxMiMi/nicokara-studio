from __future__ import annotations

import importlib.util
import shutil
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request

router = APIRouter(prefix="/settings/capabilities", tags=["settings"])


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


@router.get("")
def capabilities(request: Request):
    settings = request.app.state.settings
    providers: list[str] = []
    try:
        import onnxruntime as ort
        providers = list(ort.get_available_providers())
    except ImportError:
        pass
    providers = [provider for provider in providers if provider in {"DmlExecutionProvider", "CPUExecutionProvider"}]
    separator_devices = ["cpu"]
    if "DmlExecutionProvider" in providers:
        separator_devices.insert(0, "directml")
    whisper_devices = ["cpu"]
    models = []
    model_dir = settings.models_dir / "separator"
    if model_dir.exists():
        models = [{"name": item.name, "size": item.stat().st_size} for item in model_dir.glob("*.onnx")]
    whisper_dir = settings.models_dir / "whisper"
    whisper_models = []
    if whisper_dir.exists():
        whisper_models = [item.name for item in whisper_dir.iterdir() if item.is_dir()]
    return {
        "ffmpeg": {"available": shutil.which(settings.ffmpeg_path) is not None, "path": shutil.which(settings.ffmpeg_path)},
        "separator": {
            "available": importlib.util.find_spec("audio_separator") is not None,
            "version": package_version("audio-separator"),
            "devices": separator_devices,
            "providers": providers,
            "default_model": settings.separator_model,
            "models": models,
        },
        "whisper": {
            "available": importlib.util.find_spec("faster_whisper") is not None,
            "version": package_version("faster-whisper"),
            "devices": whisper_devices,
            "models": ["small", "medium", "turbo", "large-v3"],
            "default_model": settings.whisper_model,
            "installed_models": whisper_models,
        },
    }
