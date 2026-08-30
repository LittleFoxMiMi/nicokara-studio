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
