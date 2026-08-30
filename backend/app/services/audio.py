from __future__ import annotations

import subprocess
from pathlib import Path


class AudioProcessingError(RuntimeError):
    pass


def convert_audio(source: Path, target: Path, ffmpeg: str, *, channels: int, sample_rate: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(source), "-vn", "-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(target)],
            check=True,
            capture_output=True,
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        target.unlink(missing_ok=True)
        raise AudioProcessingError("FFmpeg 无法生成分析音频") from exc
    if not target.is_file() or target.stat().st_size == 0:
        raise AudioProcessingError("FFmpeg 生成了空音频")


def prepare_source_audio(video: Path, derived_dir: Path, ffmpeg: str) -> tuple[Path, Path]:
    source = derived_dir / "source_audio.wav"
    if not source.is_file() or source.stat().st_size == 0:
        convert_audio(video, source, ffmpeg, channels=2, sample_rate=44100)
    asr = derived_dir / "source_asr.wav"
    if not asr.is_file() or asr.stat().st_size == 0:
        convert_audio(source, asr, ffmpeg, channels=1, sample_rate=16000)
    return source, asr
