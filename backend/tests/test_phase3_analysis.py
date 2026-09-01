from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import Database
from app.domain.lyrics import parse_lyrics
from app.main import create_app
from app.services.alignment import align_document, normalize_reading, split_moras
from app.services.pipeline import AnalysisRunner
from app.services.separation import Kara2Separator, provider_for
from app.services.transcription import FasterWhisperTranscriber, Transcript, TranscriptSegment, TranscriptWord


def test_japanese_normalization_and_mora_split() -> None:
    assert normalize_reading("物語、ストーリー！") == "ものがたりすとーりー"
    assert split_moras("きゃっと") == ["きゃ", "っ", "と"]


def test_text_alignment_splits_units_and_preserves_stable_first_id() -> None:
    lyrics = parse_lyrics("君の物語", "text")
    original_id = lyrics["lines"][0]["units"][0]["id"]
    document = {"project": {"revision": 1}, "lyrics": lyrics}
    transcript = Transcript(
        "ja",
        0.99,
        4.0,
        [
            TranscriptSegment(
                0,
                "君の物語",
                1000,
                3500,
                -0.1,
                0.01,
                [TranscriptWord("君の物語", 1000, 3500, 0.95)],
            )
        ],
    )

    aligned, summary = align_document(document, transcript, preserve_line_anchors=False)
    units = aligned["lyrics"]["lines"][0]["units"]

    assert units[0]["id"] == original_id
    assert len({unit["id"] for unit in units}) == len(units)
    assert "".join(unit["surface"] for unit in units) == "君の物語"
    assert all(unit["start_ms"] is not None for unit in units)
    assert all(unit["timing_source"] == "whisper_matched" for unit in units)
    assert summary["confidence"] == pytest.approx(1.0)


def test_lrc_alignment_keeps_line_anchor() -> None:
    lyrics = parse_lyrics("[00:02.00]春風\n[00:05.00]青空", "lrc", media_duration_ms=8000)
    document = {"project": {"revision": 1}, "lyrics": lyrics}
    first = lyrics["lines"][0]
    transcript = Transcript(
        "ja", 1.0, 8.0,
        [TranscriptSegment(0, "春風", 1000, 4200, -0.1, 0.0, [TranscriptWord("春風", 1000, 4200, 1.0)])],
    )

    aligned, _ = align_document(document, transcript, line_ids=[first["id"]], preserve_line_anchors=True)

    assert aligned["lyrics"]["lines"][0]["start_ms"] == 2000
    assert aligned["lyrics"]["lines"][1] == document["lyrics"]["lines"][1]


def test_kara2_separator_requests_directml_and_places_both_stems(tmp_path, monkeypatch) -> None:
    created = []

    class FakeSeparator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

        def load_model(self, *, model_filename):
            self.model = model_filename

        def separate(self, source, names):
            directory = tmp_path / "derived"
            (directory / "generated_vocals.wav").write_bytes(b"vocals")
            (directory / "generated_instrumental.wav").write_bytes(b"instrumental")
            return ["generated_vocals.wav", "generated_instrumental.wav"]

    def fake_convert(source, target, ffmpeg, *, channels, sample_rate):
        target.write_bytes(b"asr")

    monkeypatch.setattr("app.services.separation.convert_audio", fake_convert)
    derived = tmp_path / "derived"
    derived.mkdir()
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    separator = Kara2Separator(
        tmp_path / "models", "ffmpeg",
        separator_factory=FakeSeparator,
        providers_factory=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )

    vocals, instrumental, asr = separator.separate(source, derived, device="directml")

    assert created[0].kwargs["use_directml"] is True
    assert created[0].onnx_execution_provider == ["DmlExecutionProvider"]
    assert created[0].model == "UVR_MDXNET_KARA_2.onnx"
    assert vocals.read_bytes() == b"vocals"
    assert instrumental.read_bytes() == b"instrumental"
    assert asr.read_bytes() == b"asr"
    assert provider_for("auto", ["DmlExecutionProvider", "CPUExecutionProvider"]) == "DmlExecutionProvider"


def test_whisper_auto_falls_back_to_cpu_int8_and_keeps_clip_scope(tmp_path, monkeypatch) -> None:
    calls = []

    class FakeModel:
        def transcribe(self, path, **options):
            calls.append((path, options))
            return iter([]), SimpleNamespace(language="ja", language_probability=1.0, duration=2.0)

    created = []
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")
    transcriber = FasterWhisperTranscriber(
        model_factory=lambda model, device, compute: created.append((model, device, compute)) or FakeModel()
    )

    transcriber.transcribe(audio, model_name="small", device="cpu", compute_type="int8", start_ms=1000, end_ms=2500)

    assert created == [("small", "cpu", "int8")]
    assert calls[0][1]["clip_timestamps"] == [1.0, 2.5]


def test_stable_model_uses_stable_ts_official_faster_whisper_loader(tmp_path, monkeypatch) -> None:
    calls = []

    class FakeStableWhisper:
        @staticmethod
        def load_faster_whisper(model_name, **options):
            calls.append((model_name, options))
            return object()

    monkeypatch.setitem(__import__("sys").modules, "stable_whisper", FakeStableWhisper)
    transcriber = FasterWhisperTranscriber(download_root=tmp_path / "models")

    first = transcriber.get_stable_model(model_name="large-v3", device="auto", compute_type="float16")
    second = transcriber.get_stable_model(model_name="large-v3", device="cpu", compute_type="int8")

    assert first is second
    assert calls == [("large-v3", {"device": "cpu", "compute_type": "int8", "download_root": str(tmp_path / "models")})]


def test_stable_rough_ranges_never_go_back_when_lines_exceed_segments() -> None:
    from app.services.stable_ts import rough_line_ranges

    document = {
        "lyrics": {
            "lines": [
                {"id": "line-1", "units": [{"surface": "甲"}]},
                {"id": "line-2", "units": [{"surface": "乙"}]},
                {"id": "line-3", "units": [{"surface": "丙"}]},
            ]
        }
    }
    transcript = Transcript(
        "ja",
        1.0,
        3.0,
        [
            TranscriptSegment(0, "甲", 1000, 1500, 0.9, 0.0),
            TranscriptSegment(1, "乙", 2000, 2500, 0.9, 0.0),
        ],
    )

    ranges = rough_line_ranges(document, transcript)

    assert [ranges[line_id][0] for line_id in ("line-1", "line-2", "line-3")] == [1000, 2000, 2000]


def test_stable_rough_ranges_use_ai_reading_for_special_readings() -> None:
    from app.services.stable_ts import rough_line_ranges

    document = {"lyrics": {"lines": [{"id": "line-1", "units": [{"surface": "甲", "ruby": "かな"}]}]}}
    transcript = Transcript(
        "ja",
        1.0,
        3.0,
        [
            TranscriptSegment(0, "こう", 1000, 1500, 0.9, 0.0),
            TranscriptSegment(1, "かな", 2000, 2500, 0.9, 0.0),
        ],
    )

    assert rough_line_ranges(document, transcript)["line-1"] == (2000, 2500)


def test_stable_global_and_word_alignment_are_separate_and_use_ai_reading(tmp_path, monkeypatch) -> None:
    calls = []

    class FakeAlignment:
        @staticmethod
        def align(model, audio, text, **options):
            calls.append((audio, text, options))
            return SimpleNamespace(segments=[SimpleNamespace(start=1.0, end=1.2)])

        @staticmethod
        def align_words(model, audio, result, **options):
            calls.append((audio, result, options))
            return SimpleNamespace(
                segments=[
                    SimpleNamespace(words=[SimpleNamespace(start=1.0, end=1.2, probability=0.9)])
                ]
            )

    monkeypatch.setitem(__import__("sys").modules, "stable_whisper", SimpleNamespace(alignment=FakeAlignment))
    from app.services.stable_ts import StableTSAligner

    document = {"lyrics": {"lines": [{"id": "line-1", "start_ms": None, "end_ms": None, "units": [{"id": "unit-1", "surface": "甲", "ruby": "かな", "start_ms": None, "end_ms": None}]}]}}
    aligner = StableTSAligner(lambda: object())
    global_aligned, global_summary = aligner.global_align(document, tmp_path / "vocals.wav")

    assert calls[0][1] == "かな"
    assert calls[0][2]["token_step"] == 100
    assert calls[0][2]["suppress_silence"] is True
    assert global_summary["granularity"] == "line"
    assert global_aligned["lyrics"]["lines"][0]["start_ms"] == 1000
    assert global_aligned["lyrics"]["lines"][0]["units"][0]["start_ms"] is None

    refined, refine_summary = aligner.align_words(
        global_aligned,
        tmp_path / "vocals.wav",
        segment_padding_seconds=2.0,
    )

    assert len([call for call in calls if isinstance(call[1], str)]) == 1
    assert calls[1][1][0]["text"] == "かな"
    assert calls[1][1][0]["start"] == 0.0
    assert calls[1][1][0]["end"] == 3.2
    assert "token_step" not in calls[1][2]
    assert calls[1][2]["suppress_silence"] is True
    assert refine_summary["granularity"] == "phrase"
    assert refine_summary["segment_padding_seconds"] == 2.0
    assert refined["lyrics"]["lines"][0]["units"][0]["timing_source"] == "stable_ts"


def test_stable_word_alignment_requires_global_ranges(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "stable_whisper", SimpleNamespace(alignment=SimpleNamespace()))
    from app.services.stable_ts import StableTSAligner, StableTSAlignmentError

    document = {"lyrics": {"lines": [{"id": "line-1", "start_ms": None, "end_ms": None, "units": [{"id": "unit-1", "surface": "かな"}]}]}}

    with pytest.raises(StableTSAlignmentError, match="全局对齐"):
        StableTSAligner(lambda: object()).align_words(document, tmp_path / "vocals.wav")


def test_text_import_with_video_does_not_queue_analysis_and_lrc_does_not_either(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        assert "directml" not in client.get("/api/settings/capabilities").json()["whisper"]["devices"]
        text_project = client.post("/api/projects", json={"name": "Text"}).json()
        assert client.post(
            f"/api/projects/{text_project['id']}/video",
            files={"video": ("video.mp4", b"not-real-mp4", "video/mp4")},
        ).status_code == 200
        imported = client.post(
            f"/api/projects/{text_project['id']}/lyrics/import",
            json={"revision": 2, "format": "text", "content": "春風"},
        )
        assert imported.status_code == 200
        assert imported.json()["job"] is None

        lrc_project = client.post("/api/projects", json={"name": "LRC"}).json()
        client.post(
            f"/api/projects/{lrc_project['id']}/video",
            files={"video": ("video.mp4", b"not-real-mp4", "video/mp4")},
        )
        imported_lrc = client.post(
            f"/api/projects/{lrc_project['id']}/lyrics/import",
            json={"revision": 2, "format": "lrc", "content": "[00:01.00]青空"},
        )
        assert imported_lrc.status_code == 200
        assert imported_lrc.json()["job"] is None


def test_waveform_endpoint_prefers_vocal_waveform_after_separation(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Vocal waveform"}).json()
        directory = tmp_path / "projects" / project["id"]
        (directory / "derived").mkdir(parents=True)
        (directory / "video.mp4").write_bytes(b"video")
        (directory / "waveform.json").write_text('{"source":"original"}', encoding="utf-8")
        (directory / "derived" / "vocal_waveform.json").write_text('{"source":"vocals"}', encoding="utf-8")

        response = client.get(f"/api/projects/{project['id']}/waveform")

        assert response.status_code == 200
        assert response.json() == {"source": "vocals"}


def test_range_transcription_only_accepts_known_lines(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Range"}).json()
        client.post(
            f"/api/projects/{project['id']}/video",
            files={"video": ("video.mp4", b"not-real-mp4", "video/mp4")},
        )
        imported = client.post(
            f"/api/projects/{project['id']}/lyrics/import",
            json={"revision": 2, "format": "lrc", "content": "[00:01.00]青空"},
        ).json()
        line_id = imported["document"]["lyrics"]["lines"][0]["id"]
        calls = []
        client.app.state.analysis_runner = SimpleNamespace(
            enqueue=lambda *args: calls.append(args) or {"id": "range-job", "status": "QUEUED"}
        )
        accepted = client.post(
            f"/api/projects/{project['id']}/transcribe",
            json={"revision": 3, "line_ids": [line_id], "start_ms": 1000, "end_ms": 3000},
        )
        rejected = client.post(
            f"/api/projects/{project['id']}/transcribe",
            json={"revision": 3, "line_ids": ["unknown"]},
        )
        assert accepted.status_code == 202
        assert calls[0][1] == "TRANSCRIPTION"
        assert rejected.status_code == 422


def test_job_runner_persists_success(tmp_path) -> None:
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    database.create_project("project", "Project", {"project": {"revision": 1}})

    class FakePipeline:
        def process(self, job_id):
            return {"output_revision": 2, "confidence": 0.9}

    runner = AnalysisRunner(database, FakePipeline())
    job = runner.enqueue("project", "TRANSCRIPTION", 1, {})
    for _ in range(100):
        stored = database.get_job(job["id"])
        if stored and stored["status"] == "SUCCEEDED":
            break
        time.sleep(0.01)
    runner.shutdown()
    assert stored["output_revision"] == 2
    assert stored["result"]["confidence"] == 0.9


def test_full_analysis_rejects_skipping_incomplete_steps(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Full analysis"}).json()
        client.post(f"/api/projects/{project['id']}/video", files={"video": ("video.mp4", b"not-real-mp4", "video/mp4")})
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 2, "format": "text", "content": "春風"}).json()
        calls = []
        client.app.state.analysis_runner = SimpleNamespace(enqueue=lambda *args: calls.append(args) or {"id": "full-job", "status": "QUEUED"})
        rejected = client.post(f"/api/projects/{project['id']}/analysis", json={"revision": imported["revision"], "alignment_backend": "stable_ts", "steps": ["alignment"]})
        assert rejected.status_code == 422
        assert "不能跳过未完成" in rejected.text
        assert calls == []


def test_stable_alignment_endpoint_requires_global_alignment_only(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Split stable-ts"}).json()
        client.post(f"/api/projects/{project['id']}/video", files={"video": ("video.mp4", b"not-real-mp4", "video/mp4")})
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 2, "format": "text", "content": "かな"}).json()
        directory = tmp_path / "projects" / project["id"] / "derived"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "vocals_asr.wav").write_bytes(b"wav")
        (directory / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
        document = imported["document"]
        document.setdefault("analysis", {})["transcription"] = {"status": "completed"}
        saved = client.app.state.database.save_document(project["id"], document, imported["revision"])
        calls = []
        client.app.state.analysis_runner = SimpleNamespace(enqueue=lambda *args: calls.append(args) or {"id": "stable-job", "status": "QUEUED"})

        global_response = client.post(f"/api/projects/{project['id']}/align-global", json={"revision": saved["revision"]})
        removed_line_refinement = client.post(f"/api/projects/{project['id']}/refine-lines", json={"revision": saved["revision"]})
        phrase_refine_rejected = client.post(f"/api/projects/{project['id']}/align", json={"revision": saved["revision"]})

        assert global_response.status_code == 202
        assert calls[0][1] == "STABLE_GLOBAL_ALIGNMENT"
        assert removed_line_refinement.status_code == 404
        assert phrase_refine_rejected.status_code == 422
        assert "全局对齐" in phrase_refine_rejected.text

        document = saved["document"]
        document["analysis"]["global_alignment"] = {"status": "completed"}
        document["lyrics"]["lines"][0]["start_ms"] = 1000
        document["lyrics"]["lines"][0]["end_ms"] = 2000
        (directory / "stable_global.json").write_text("{}", encoding="utf-8")
        saved = client.app.state.database.save_document(project["id"], document, saved["revision"])
        phrase_refine_accepted = client.post(f"/api/projects/{project['id']}/align", json={"revision": saved["revision"]})

        assert phrase_refine_accepted.status_code == 202
        assert calls[-1][1] == "STABLE_ALIGNMENT"


def test_stable_ts_settings_validate_token_step_and_segment_padding(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        valid = client.put(
            "/api/settings",
            json={"values": {"stable_ts_token_step": 0, "stable_ts_segment_padding_seconds": 2.5}},
        )
        assert valid.status_code == 200
        assert valid.json()["stable_ts_token_step"] == 0
        assert valid.json()["stable_ts_segment_padding_seconds"] == 2.5

        invalid_token_step = client.put("/api/settings", json={"values": {"stable_ts_token_step": 443}})
        invalid_padding = client.put("/api/settings", json={"values": {"stable_ts_segment_padding_seconds": -1}})
        assert invalid_token_step.status_code == 422
        assert invalid_padding.status_code == 422


def test_job_steps_are_persisted_per_task(tmp_path) -> None:
    database = Database(tmp_path / "steps.sqlite3")
    database.initialize()
    database.create_project("project", "Project", {"project": {"revision": 1}})
    job = database.create_job("job", "project", "FULL_ANALYSIS", 1, {"steps": ["separation", "transcription"]})
    assert [item["key"] for item in job["steps"]] == ["separation", "transcription"]
    database.update_job("job", steps=[{"key": "separation", "label": "KARA2", "status": "running", "progress": 0.5}])
    assert database.get_job("job")["steps"][0]["progress"] == 0.5
    global_job = database.create_job("global", "project", "STABLE_GLOBAL_ALIGNMENT", 1, {})
    assert global_job["steps"][0]["key"] == "global_alignment"
