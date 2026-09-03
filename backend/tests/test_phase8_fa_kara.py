from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_copy_project_copies_assets_but_skips_exports(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        source = client.post("/api/projects", json={"name": "原工程"}).json()
        document = client.get(f"/api/projects/{source['id']}/document").json()["document"]
        document["media"].update({"video_filename": "video.mp4", "thumbnail_url": f"/api/projects/{source['id']}/thumbnail", "waveform_url": f"/api/projects/{source['id']}/waveform"})
        saved = client.put(f"/api/projects/{source['id']}/document", json={"revision": 1, "document": document})
        assert saved.status_code == 200
        source_dir = tmp_path / "projects" / source["id"]
        (source_dir / "derived").mkdir(parents=True)
        (source_dir / "exports").mkdir()
        (source_dir / "video.mp4").write_bytes(b"video")
        (source_dir / "derived" / "fa_kara.json").write_text("{}", encoding="utf-8")
        (source_dir / "exports" / "large.mp4").write_bytes(b"should not copy")

        response = client.post(f"/api/projects/{source['id']}/copy")
        assert response.status_code == 201
        copied = response.json()
        assert copied["id"] != source["id"]
        assert copied["name"] == "原工程 (副本)"
        copied_dir = tmp_path / "projects" / copied["id"]
        assert (copied_dir / "video.mp4").read_bytes() == b"video"
        assert (copied_dir / "derived" / "fa_kara.json").is_file()
        assert not (copied_dir / "exports").exists()
        copied_document = client.get(f"/api/projects/{copied['id']}/document").json()["document"]
        assert copied_document["project"]["id"] == copied["id"]
        assert copied_document["project"]["revision"] == 1
        assert copied_document["media"]["thumbnail_url"].endswith(f"/{copied['id']}/thumbnail")


def test_fa_kara_uses_ai_ruby_and_converts_to_romaji(tmp_path, monkeypatch):
    from app.services.fa_kara import FAKaraAligner, FAKaraAlignmentError, _romaji
    import numpy as np
    import soundfile
    import torch

    assert _romaji("きのう") == "kinou"
    audio = tmp_path / "vocals.wav"
    audio.write_bytes(b"placeholder")

    class FakeModel:
        def parameters(self):
            return iter([torch.nn.Parameter(torch.zeros(1))])

        def __call__(self, waveform):
            return torch.zeros((1, 30, 28)), None

    class FakeBundle:
        sample_rate = 16000

        @staticmethod
        def get_tokenizer():
            return lambda values: [[1] * len(value) for value in values]

        @staticmethod
        def get_aligner():
            from torchaudio.functional import TokenSpan

            def align(_emission, tokens):
                cursor = 0
                result = []
                for token_group in tokens:
                    result.append([TokenSpan(1, cursor, cursor + len(token_group), 0.9)])
                    cursor += len(token_group)
                return result

            return align

    monkeypatch.setattr(soundfile, "read", lambda *_args, **_kwargs: (np.zeros((16000, 1), dtype=np.float32), 16000))
    document = {"lyrics": {"lines": [{"id": "line", "units": [
        {"id": "u1", "surface": "昨日", "ruby": "きのう", "ruby_span": 2, "locked": False},
        {"id": "u2", "surface": "は", "ruby": None, "locked": False},
    ]}]}}
    updated, summary = FAKaraAligner(lambda: (FakeModel(), FakeBundle())).align(document, audio)
    assert summary["engine"] == "fa-kara"
    assert summary["selected_targets"] == 2
    assert updated["lyrics"]["lines"][0]["units"][0]["timing_source"] == "fa_kara"
    assert updated["lyrics"]["lines"][0]["units"][0]["end_ms"] > 0

    offset_updated, offset_summary = FAKaraAligner(lambda: (FakeModel(), FakeBundle())).align(
        document, audio, time_offset_ms=5000
    )
    assert offset_updated["lyrics"]["lines"][0]["units"][0]["start_ms"] >= 5000
    assert offset_summary["time_offset_ms"] == 5000

    missing = {"lyrics": {"lines": [{"id": "line", "units": [{"surface": "昨日", "ruby": None}]}]}}
    with pytest.raises(FAKaraAlignmentError, match="AI 注音"):
        FAKaraAligner(lambda: (FakeModel(), FakeBundle())).align(missing, audio)


def test_fa_kara_endpoint_is_independent_from_stable_ts(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "FA test"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        document["media"]["video_filename"] = "video.mp4"
        document["lyrics"]["lines"] = [{"id": "line", "units": [{"id": "unit", "surface": "かな", "ruby": None, "locked": False}]}]
        document.setdefault("analysis", {})["transcription"] = {"status": "completed"}
        saved = client.put(f"/api/projects/{project['id']}/document", json={"revision": 1, "document": document}).json()
        derived = tmp_path / "projects" / project["id"] / "derived"
        derived.mkdir(parents=True)
        (derived / "vocals_asr.wav").write_bytes(b"audio")
        (derived / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
        client.put("/api/settings", json={"values": {"fa_kara_model": "yohane"}})
        client.app.state.analysis_runner = SimpleNamespace(enqueue=lambda *args: {"id": "fa-job", "type": args[1], "request": args[3]})
        response = client.post(f"/api/projects/{project['id']}/fa-kara", json={"revision": saved["revision"]})
        assert response.status_code == 202
        assert response.json()["type"] == "FA_KARA_ALIGNMENT"
        assert response.json()["request"]["model"] == "yohane"


def test_fa_kara_full_analysis_keeps_whisper_and_pronunciation_steps(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "FA full"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        document["media"]["video_filename"] = "video.mp4"
        document["lyrics"]["lines"] = [{"id": "line", "units": [{"id": "unit", "surface": "かな", "ruby": None}]}]
        saved = client.put(f"/api/projects/{project['id']}/document", json={"revision": 1, "document": document}).json()
        calls = []
        client.app.state.analysis_runner = SimpleNamespace(enqueue=lambda *args: calls.append(args) or {"id": "full-job", "status": "QUEUED", "request": args[3]})
        response = client.post(
            f"/api/projects/{project['id']}/analysis",
            json={
                "revision": saved["revision"],
                "alignment_backend": "fa_kara",
                "fa_kara_model": "yohane",
                "steps": ["separation", "transcription", "pronunciation", "fa_kara"],
            },
        )
        assert response.status_code == 202
        assert response.json()["request"]["steps"] == ["separation", "transcription", "pronunciation", "fa_kara"]
        assert response.json()["request"]["fa_kara_model"] == "yohane"


def test_full_analysis_with_missing_ruby_stops_only_when_alignment_begins(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "全曲补注音"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        document["media"]["video_filename"] = "video.mp4"
        document["lyrics"]["lines"] = [{"id": "line", "units": [{"id": "unit", "surface": "雨", "ruby": None}]}]
        saved = client.put(
            f"/api/projects/{project['id']}/document",
            json={"revision": 1, "document": document},
        ).json()
        pipeline = client.app.state.analysis_runner.pipeline
        calls = []
        pipeline._separate = lambda job_id, project_id, current, revision, payload, values: (calls.append("separation") or current, revision, {})
        pipeline._transcribe = lambda job_id, project_id, current, revision, payload, values: (calls.append("transcription") or current, revision, {})
        pipeline._pronounce = lambda job_id, project_id, current, revision, payload: (calls.append("pronunciation") or current, revision, {})
        pipeline._fa_kara = lambda *args: calls.append("fa_kara")

        queued = client.post(
            f"/api/projects/{project['id']}/analysis",
            json={
                "revision": saved["revision"],
                "alignment_backend": "fa_kara",
                "steps": ["separation", "transcription", "pronunciation", "fa_kara"],
            },
        )
        assert queued.status_code == 202
        for _ in range(100):
            job = client.get(f"/api/jobs/{queued.json()['id']}").json()
            if job["status"] == "FAILED":
                break
            time.sleep(0.01)

        assert job["error_code"] == "missing_ruby"
        assert calls == ["separation", "transcription", "pronunciation"]
        assert "雨" in job["error_message"]


def test_ai_pronunciation_job_adds_whisper_prerequisites_only_when_missing(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "AI 注音前置"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        document["media"]["video_filename"] = "video.mp4"
        document["lyrics"]["lines"] = [{"id": "line", "units": [{"id": "unit", "surface": "雨", "ruby": None}]}]
        saved = client.put(
            f"/api/projects/{project['id']}/document",
            json={"revision": 1, "document": document},
        ).json()
        calls = []
        client.app.state.analysis_runner = SimpleNamespace(
            enqueue=lambda *args: calls.append(args) or {"id": f"job-{len(calls)}", "request": args[3]}
        )

        fresh = client.post(
            f"/api/projects/{project['id']}/pronunciation-job",
            json={"revision": saved["revision"], "mode": "ai"},
        )
        assert fresh.status_code == 202
        assert fresh.json()["request"]["steps"] == ["separation", "transcription", "pronunciation"]

        derived = tmp_path / "projects" / project["id"] / "derived"
        derived.mkdir(parents=True)
        (derived / "vocals_asr.wav").write_bytes(b"audio")
        (derived / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
        document.setdefault("analysis", {})["transcription"] = {"status": "completed"}
        saved = client.app.state.database.save_document(project["id"], document, saved["revision"])

        existing = client.post(
            f"/api/projects/{project['id']}/pronunciation-job",
            json={"revision": saved["revision"], "mode": "ai"},
        )
        local = client.post(
            f"/api/projects/{project['id']}/pronunciation-job",
            json={"revision": saved["revision"], "mode": "local"},
        )
        assert existing.status_code == 202
        assert existing.json()["request"]["steps"] == ["pronunciation"]
        assert local.status_code == 202
        assert local.json()["request"]["steps"] == ["pronunciation"]


def test_fresh_ai_pronunciation_job_runs_separation_whisper_and_ai_in_order(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "新工程 AI 注音"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        document["media"]["video_filename"] = "video.mp4"
        document["lyrics"]["lines"] = [{"id": "line", "units": [{"id": "unit", "surface": "雨", "ruby": None}]}]
        saved = client.put(
            f"/api/projects/{project['id']}/document",
            json={"revision": 1, "document": document},
        ).json()
        pipeline = client.app.state.analysis_runner.pipeline
        calls = []
        pipeline._separate = lambda job_id, project_id, current, revision, payload, values: (calls.append("separation") or current, revision, {})
        pipeline._transcribe = lambda job_id, project_id, current, revision, payload, values: (calls.append("transcription") or current, revision, {})
        pipeline._pronounce = lambda job_id, project_id, current, revision, payload: (calls.append("pronunciation") or current, revision, {})

        queued = client.post(
            f"/api/projects/{project['id']}/pronunciation-job",
            json={"revision": saved["revision"], "mode": "ai"},
        )
        assert queued.status_code == 202
        for _ in range(100):
            job = client.get(f"/api/jobs/{queued.json()['id']}").json()
            if job["status"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.01)

        assert job["status"] == "SUCCEEDED"
        assert calls == ["separation", "transcription", "pronunciation"]


def test_fa_kara_settings_validate_backend_and_model(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        valid = client.put("/api/settings", json={"values": {"alignment_backend": "fa_kara", "fa_kara_model": "yohane"}})
        assert valid.status_code == 200
        assert valid.json()["alignment_backend"] == "fa_kara"
        assert valid.json()["fa_kara_model"] == "yohane"
        assert client.put("/api/settings", json={"values": {"alignment_backend": "unknown"}}).status_code == 422
        assert client.put("/api/settings", json={"values": {"fa_kara_model": "unknown"}}).status_code == 422


def test_chinese_project_uses_pinyin_and_skips_pronunciation(tmp_path, monkeypatch):
    from app.services.fa_kara import _groups, _prepare_chinese_units

    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    groups = _groups({"units": [{"surface": "中国", "ruby": "ちゅうごく"}]}, language="cn")
    assert groups[0][1] == "zhongguo"
    lines = [{"units": [{"id": "unit", "surface": "银行", "ruby": "ぎんこう", "start_ms": 0, "end_ms": 1000}]}]
    _prepare_chinese_units(lines)
    assert [unit["surface"] for unit in lines[0]["units"]] == ["银", "行"]
    assert [unit["alignment_reading"] for unit in lines[0]["units"]] == ["yin", "hang"]
    assert all(unit["ruby"] is None for unit in lines[0]["units"])

    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "中文工程"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        document["project"]["language"] = "cn"
        document["lyrics"]["lines"] = [{"id": "line", "units": [{"id": "unit", "surface": "中国", "ruby": "ちゅうごく"}]}]
        document["media"]["video_filename"] = "video.mp4"
        saved = client.put(f"/api/projects/{project['id']}/document", json={"revision": 1, "document": document})
        assert saved.status_code == 200
        current = client.get(f"/api/projects/{project['id']}/document").json()
        normalized = current["document"]
        assert normalized["project"]["language"] == "cn"
        assert normalized["lyrics"]["lines"][0]["units"][0]["ruby"] is None
        client.app.state.analysis_runner = SimpleNamespace(enqueue=lambda *args: {"id": "full-cn", "request": args[3]})
        full = client.post(
            f"/api/projects/{project['id']}/analysis",
            json={"revision": current["revision"], "alignment_backend": "stable_ts", "steps": ["separation", "transcription", "pronunciation", "fa_kara"]},
        )
        assert full.status_code == 202
        assert full.json()["request"]["steps"] == ["separation", "transcription", "fa_kara"]
        pronunciation = client.post(
            f"/api/projects/{project['id']}/pronunciation-job",
            json={"revision": full.json().get("request", {}).get("revision", current["revision"]), "mode": "local"},
        )
        assert pronunciation.status_code == 422


def test_japanese_alignment_rejects_missing_ruby_and_accepts_ruby_span(tmp_path, monkeypatch):
    from app.services.fa_kara_text import missing_japanese_ruby

    assert missing_japanese_ruby([{"id": "line", "units": [
        {"id": "u1", "surface": "昨", "ruby": "きのう", "ruby_span": 2},
        {"id": "u2", "surface": "日", "ruby": None},
        {"id": "u3", "surface": "雨", "ruby": None},
    ]}]) == [{"line_index": 0, "line_id": "line", "unit_id": "u3", "characters": "雨"}]

    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "日文漏注音"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        document["project"]["language"] = "jp"
        document["media"]["video_filename"] = "video.mp4"
        document["lyrics"]["lines"] = [{"id": "line", "units": [{"id": "u1", "surface": "雨", "ruby": None}]}]
        saved = client.put(f"/api/projects/{project['id']}/document", json={"revision": 1, "document": document}).json()
        response = client.post(f"/api/projects/{project['id']}/fa-kara", json={"revision": saved["revision"]})
        assert response.status_code == 422
        assert "雨" in response.text
