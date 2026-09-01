from pathlib import Path

from app.core.config import Settings
from app.services.kirakara_export import build_worker_html, document_to_lrc, export_output_path


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


def test_document_to_lrc_emits_one_ruby_for_a_multi_unit_word() -> None:
    document = {
        "lyrics": {
            "lines": [{
                "start_ms": 0,
                "end_ms": 400,
                "units": [
                    {"surface": "二", "ruby": "にほん", "ruby_span": 2, "start_ms": 0, "end_ms": 200, "roles": []},
                    {"surface": "本", "ruby": None, "ruby_span": 0, "start_ms": 200, "end_ms": 400, "roles": []},
                ],
            }],
        },
    }
    assert document_to_lrc(document) == "[00:00.00]{二本|にほん}[00:00.40]"


def test_document_to_lrc_coalesces_legacy_repeated_ruby() -> None:
    document = {
        "lyrics": {
            "lines": [{
                "start_ms": 0,
                "end_ms": 400,
                "units": [
                    {"surface": "二", "ruby": "にほん", "start_ms": 0, "end_ms": 200, "roles": []},
                    {"surface": "本", "ruby": "にほん", "start_ms": 200, "end_ms": 400, "roles": []},
                ],
            }],
        },
    }
    assert document_to_lrc(document) == "[00:00.00]{二本|にほん}[00:00.40]"


def test_document_to_lrc_treats_ruby_span_as_surface_characters() -> None:
    document = {
        "lyrics": {
            "lines": [{
                "start_ms": 0,
                "end_ms": 600,
                "units": [
                    {"surface": "日本", "ruby": "にほん", "ruby_span": 2, "start_ms": 0, "end_ms": 300, "roles": []},
                    {"surface": "語", "ruby": None, "ruby_span": 0, "start_ms": 300, "end_ms": 600, "roles": []},
                ],
            }],
        },
    }
    assert document_to_lrc(document) == "[00:00.00]{日本|にほん}[00:00.30]語[00:00.60]"


def test_document_to_lrc_places_untimed_spaces_after_previous_unit_end() -> None:
    document = {
        "lyrics": {
            "lines": [{
                "start_ms": 116780,
                "end_ms": 120000,
                "units": [
                    {"surface": "日常", "ruby": "にちじょう", "start_ms": 117960, "end_ms": 118760, "roles": []},
                    {"surface": " ", "ruby": None, "start_ms": None, "end_ms": None, "roles": []},
                    {"surface": "留", "ruby": "とど", "start_ms": 119540, "end_ms": 119880, "roles": []},
                ],
            }],
        },
    }

    assert document_to_lrc(document) == (
        "[01:57.96]{日常|にちじょう}[01:58.76] [01:59.54]{留|とど}[02:00.00]"
    )
