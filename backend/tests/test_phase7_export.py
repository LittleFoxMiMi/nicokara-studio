from pathlib import Path
import time

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.config import get_settings
from app.domain.lyrics.parser import parse_lyrics
from app.main import create_app
from app.services.kirakara_export import _encode_with_ffmpeg, _encoding_args, build_worker_html, document_to_krl, document_to_lrc, export_output_path, run_kirakara_export


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
    assert export_output_path(settings, "project", "12345678-job", "krl").name == "12345678.krl"


def test_krl_export_contains_config_lyrics_roles_and_dual_ruby(tmp_path: Path) -> None:
    document = _document()
    document["lyrics"]["lines"] = [{
        "start_ms": 1000,
        "end_ms": 2000,
        "units": [{
            "surface": "物語",
            "ruby": "ものがたり",
            "ruby_2": "モノガタリ",
            "ruby_span": 2,
            "start_ms": 1000,
            "end_ms": 2000,
            "roles": ["Lead", "Chorus"],
        }],
    }]

    content = document_to_krl(document)

    assert content.startswith("config {")
    assert '"fontFamily"' in content
    assert "[00:01.00]【@Lead+Chorus】{物語|ものがたり>モノガタリ}[00:02.00]" in content
    imported = parse_lyrics(content.split("\n\n\n", 1)[1], "krl", filename="roundtrip.krl")
    assert [unit["surface"] for unit in imported["lines"][0]["units"]] == ["物", "語"]
    assert imported["lines"][0]["units"][0]["ruby"] == "ものがたり"
    assert imported["lines"][0]["units"][0]["ruby_2"] == "モノガタリ"
    assert imported["lines"][0]["units"][0]["roles"] == ["Lead", "Chorus"]

    settings = Settings(data_dir=tmp_path)
    result = run_kirakara_export("12345678-job", "project", document, {"format": "krl"}, settings)
    output = export_output_path(settings, "project", "12345678-job", "krl")
    assert output.read_text(encoding="utf-8") == content
    assert result["filename"] == "测试工程.krl"
    assert result["media_type"] == "text/plain; charset=utf-8"


def test_krl_round_trip_preserves_silent_gap_inside_ruby() -> None:
    ruby_timing = [
        {"text": "きょ", "start_ms": 1000, "end_ms": 1175},
        {"text": "う", "start_ms": 1425, "end_ms": 1600},
    ]
    document = _document()
    document["lyrics"]["lines"] = [{
        "start_ms": 1000, "end_ms": 2000, "timing_precision": "phrase",
        "units": [
            {"surface": "今日", "ruby": "きょう", "ruby_span": 2, "ruby_timing": ruby_timing, "start_ms": 1000, "end_ms": 1600},
            {"surface": "は", "start_ms": 1800, "end_ms": 2000},
        ],
    }]
    exported = document_to_lrc(document)
    assert "{今日|[00:01.000]きょ[00:01.175][00:01.425]う[00:01.600]}" in exported
    units = parse_lyrics(exported, "krl")["lines"][0]["units"]
    assert units[0]["ruby_timing"] == ruby_timing
    assert units[1]["end_ms"] == 1600
    assert units[2]["start_ms"] == 1800


def test_krl_export_uses_line_bounds_when_edited_units_are_untimed() -> None:
    document = _document()
    document["lyrics"]["lines"] = [{
        "start_ms": 1000, "end_ms": 2200, "timing_precision": "line",
        "units": [
            {"surface": "あ", "start_ms": None, "end_ms": None},
            {"surface": "今日", "ruby": "きょう", "ruby_span": 2, "start_ms": None, "end_ms": None},
            {"surface": "も", "start_ms": None, "end_ms": None},
        ],
    }]
    assert document_to_lrc(document) == "[00:01.00]あ[00:01.30]{今日|きょう}[00:01.90]も[00:02.20]"


def test_krl_silent_punctuation_uses_sung_boundaries() -> None:
    units = [
        {"surface": "「（", "start_ms": 1000, "end_ms": 1100},
        {"surface": "あ", "start_ms": 1100, "end_ms": 1300},
        {"surface": "、…", "start_ms": None, "end_ms": None},
        {"surface": "今日", "ruby": "きょう", "ruby_span": 2, "start_ms": 1700, "end_ms": 2100,
         "ruby_timing": [{"text": "きょ", "start_ms": 1800, "end_ms": 1930},
                         {"text": "う", "start_ms": 1950, "end_ms": 2000}]},
        {"surface": "！」", "start_ms": 2100, "end_ms": 2300},
    ]
    document = {"lyrics": {"lines": [{"start_ms": 1000, "end_ms": 2300, "timing_precision": "phrase", "units": units}]}}
    exported = document_to_lrc(document)
    imported = parse_lyrics(exported, "krl")["lines"][0]["units"]
    assert [(unit["surface"], unit["start_ms"], unit["end_ms"]) for unit in imported] == [
        ("「", 1100, 1100), ("（", 1100, 1100), ("あ", 1100, 1300),
        ("、", 1300, 1550), ("…", 1550, 1800), ("今", 1800, 1900), ("日", 1900, 2000),
        ("！", 2000, 2000), ("」", 2000, 2000),
    ]
    assert imported[5]["ruby_timing"] == units[3]["ruby_timing"]


def test_krl_export_endpoint_accepts_project_without_video(tmp_path: Path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    captured = {}
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Lyrics only"}).json()

        def enqueue(project_id, job_type, revision, payload):
            captured.update({"project_id": project_id, "job_type": job_type, "revision": revision, "payload": payload})
            return {"id": "job", "request": payload}

        client.app.state.analysis_runner.enqueue = enqueue
        response = client.post(
            f"/api/projects/{project['id']}/export",
            json={"revision": project["revision"], "format": "krl", "audio_track": "off_vocal"},
        )

        assert response.status_code == 202
        assert captured == {
            "project_id": project["id"],
            "job_type": "EXPORT",
            "revision": project["revision"],
            "payload": {"revision": project["revision"], "format": "krl", "steps": ["export"]},
        }


def test_krl_export_job_can_be_downloaded_without_chrome_or_video(tmp_path: Path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NICOKARA_CHROME_PATH", str(tmp_path / "missing-chrome.exe"))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "No browser needed"}).json()
        queued = client.post(
            f"/api/projects/{project['id']}/export",
            json={"revision": project["revision"], "format": "krl"},
        ).json()

        job = queued
        for _ in range(100):
            job = client.get(f"/api/jobs/{queued['id']}").json()
            if job["status"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.01)

        assert job["status"] == "SUCCEEDED"
        download = client.get(f"/api/projects/{project['id']}/exports/{queued['id']}/download")
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("text/plain")
        assert download.content.decode("utf-8").startswith("config {")


def test_ffmpeg_encoding_args_use_export_settings() -> None:
    video, audio = _encoding_args(
        "mp4",
        30,
        {"video_crf": 17, "h264_preset": "slow", "audio_bitrate_kbps": 256, "gop_seconds": 1.5},
    )
    assert video[:6] == ["-c:v", "libx264", "-preset", "slow", "-crf", "17"]
    assert ["-g", "45"] == video[6:8]
    assert ["-b:a", "256k"] == audio[2:4]

    video, audio = _encoding_args(
        "webm",
        60,
        {"video_crf": 28, "vp9_cpu_used": 4, "audio_bitrate_kbps": 160, "gop_seconds": 2},
    )
    assert ["-crf", "28"] == video[2:4]
    assert ["-cpu-used", "4"] == video[8:10]
    assert ["-g", "120"] == video[-2:]
    assert audio == ["-c:a", "libopus", "-b:a", "160k"]


def test_first_video_pass_has_no_audio_and_second_pass_uses_same_stats(tmp_path: Path, monkeypatch) -> None:
    commands = []

    def run_pass(command, *_args):
        commands.append(command)
        if command[command.index("-pass") + 1] == "2":
            Path(command[-1]).write_bytes(b"video")

    monkeypatch.setattr("app.services.kirakara_export._run_encoding_pass", run_pass)
    output = tmp_path / "output.mp4"
    _encode_with_ffmpeg(tmp_path / "raw.mp4", output, tmp_path / "audio.wav", "mp4", "ffmpeg", 24, 1000, {})

    first, second = commands
    assert first[first.index("-pass") + 1] == "1"
    assert second[second.index("-pass") + 1] == "2"
    assert first[first.index("-passlogfile") + 1] == second[second.index("-passlogfile") + 1]
    assert first[-4:] == ["-an", "-f", "null", "-"]
    assert str(tmp_path / "audio.wav") not in first
    assert str(tmp_path / "audio.wav") in second
    for command in commands:
        assert command[command.index("-b:v") + 1] == "24000k"
        assert command[command.index("-maxrate") + 1] == "48000k"
    assert output.read_bytes() == b"video"
    assert list(tmp_path.iterdir()) == [output]


def test_off_vocal_export_enqueues_deferred_separation_with_saved_defaults(tmp_path: Path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    captured = {}
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Deferred export"}).json()
        current = client.get(f"/api/projects/{project['id']}/document").json()
        document = current["document"]
        document["media"]["video_filename"] = "video.mp4"
        saved = client.put(
            f"/api/projects/{project['id']}/document",
            json={"revision": current["revision"], "document": document},
        ).json()
        settings_response = client.put(
            "/api/settings",
            json={"values": {
                "separator_instrumental_model": "UVR_MDXNET_KARA_2.onnx",
                "export_mp4_crf": 18,
                "export_video_target_bitrate_kbps": 32000,
                "export_video_max_bitrate_kbps": 64000,
                "export_h264_preset": "slow",
                "export_audio_bitrate_kbps": 256,
                "export_gop_seconds": 1.5,
            }},
        )
        assert settings_response.status_code == 200
        assert client.put("/api/settings", json={"values": {"export_video_max_bitrate_kbps": 16000}}).status_code == 422

        def enqueue(project_id, job_type, revision, payload):
            captured.update({"project_id": project_id, "job_type": job_type, "revision": revision, "payload": payload})
            return {"id": "job", "request": payload}

        client.app.state.analysis_runner.enqueue = enqueue
        response = client.post(
            f"/api/projects/{project['id']}/export",
            json={"revision": saved["revision"], "format": "mp4", "audio_track": "off_vocal"},
        )

        assert response.status_code == 202
        assert not (tmp_path / "projects" / project["id"] / "derived" / "instrumental.wav").exists()
        assert captured["payload"]["separator_instrumental_model"] == "UVR_MDXNET_KARA_2.onnx"
        assert captured["payload"]["video_crf"] == 18
        assert captured["payload"]["video_rate_control"] == "two_pass"
        assert captured["payload"]["video_target_bitrate_kbps"] == 32000
        assert captured["payload"]["video_max_bitrate_kbps"] == 64000
        assert captured["payload"]["h264_preset"] == "slow"
        assert captured["payload"]["audio_bitrate_kbps"] == 256
        assert captured["payload"]["gop_seconds"] == 1.5


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
