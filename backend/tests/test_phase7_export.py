from pathlib import Path

from app.core.config import Settings
from app.services.kirakara_export import build_worker_html, export_output_path


def _document() -> dict:
    return {
        "project": {"name": "测试工程"},
        "media": {"width": 320, "height": 240, "fps": "30/1", "duration_ms": 1000, "video_filename": "video.mp4"},
        "lyrics": {"lines": []},
        "styles": {},
    }


def test_worker_html_accepts_fractional_ffprobe_fps(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, export_base_url="http://127.0.0.1:8100")
    html = build_worker_html(settings, "project", "job", _document(), {"format": "webm", "audio_track": "on_vocal"})
    assert "fps:d.fps" in html
    assert "expCodec:d.format==='mp4'?'h264':'vp9'" in html
    assert "/api/projects/project/video" in html


def test_export_output_path_keeps_selected_video_format(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    assert export_output_path(settings, "project", "12345678-job", "mp4").name == "12345678.mp4"
    assert export_output_path(settings, "project", "12345678-job", "webm").name == "12345678.webm"
