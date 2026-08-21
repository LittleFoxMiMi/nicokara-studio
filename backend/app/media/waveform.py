from __future__ import annotations

import array
import json
import subprocess
import sys
from pathlib import Path


def generate_waveform(video: Path, target: Path, ffmpeg: str, peak_count: int = 1600) -> bool:
    """Decode a low-rate mono stream and persist display-ready min/max peaks."""
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            capture_output=True,
            timeout=180,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        target.unlink(missing_ok=True)
        return False

    samples = array.array("h")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        target.unlink(missing_ok=True)
        return False

    bucket_size = max(1, (len(samples) + peak_count - 1) // peak_count)
    peaks: list[list[float]] = []
    for offset in range(0, len(samples), bucket_size):
        bucket = samples[offset : offset + bucket_size]
        peaks.append([round(min(bucket) / 32768, 4), round(max(bucket) / 32768, 4)])
    payload = {
        "version": 1,
        "sample_rate": 8000,
        "duration_ms": round(len(samples) / 8000 * 1000),
        "peaks": peaks,
    }
    target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return True
