from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
import httpx

from app.core.config import get_settings
from app.main import create_app
from app.services.pronunciation import PronunciationSelection, PronunciationValidationError, apply_ai, chunk_lines, validate_ai_result


def test_chunk_lines_preserves_line_boundaries_and_long_lines():
    lines = [{"line_index": 0, "text": "雨"}, {"line_index": 1, "text": "青空"}, {"line_index": 2, "text": "長い歌詞"}]
    batches = chunk_lines(lines, 3)
    assert [[item["line_index"] for item in batch] for batch in batches] == [[0, 1], [2]]
    assert batches[1][0]["text"] == "長い歌詞"


def test_ai_pronunciation_batches_and_retries_structural_failure(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        profile = client.post("/api/settings/ai-profiles", json={"name": "Batch", "base_url": "http://localhost:1234/v1", "model": "demo", "api_key": "secret", "max_chars_per_request": 100, "retry_count": 1}).json()
        assert profile["max_chars_per_request"] == 100
        assert profile["retry_count"] == 1
        project = client.post("/api/projects", json={"name": "Batching"}).json()
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 1, "format": "text", "content": "a" * 80 + "\n" + "b" * 80}).json()
        calls = []

        def fake_complete(self, system_prompt, user_prompt):
            calls.append(user_prompt)
            if len(calls) == 2:
                return {"invalid": []}
            line_index = 0 if len(calls) == 1 else 1
            text = "a" * 80 if line_index == 0 else "b" * 80
            return {"result": [{"line_index": line_index, "raw": text, "pronunciation": []}]}

        monkeypatch.setattr("app.api.pronunciation.AIClient.complete", fake_complete)
        response = client.post(f"/api/projects/{project['id']}/pronunciation/ai", json={"revision": imported["revision"], "mode": "ai", "profile_id": profile["id"]})
        assert response.status_code == 200
        assert response.json()["summary"]["batch_count"] == 2
        assert response.json()["summary"]["retry_count"] == 1
        assert len(calls) == 3


def test_ai_pronunciation_rejects_noncanonical_raw_protocol(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        profile = client.post("/api/settings/ai-profiles", json={"name": "Partial", "base_url": "http://localhost:1234/v1", "model": "demo", "api_key": "secret", "max_chars_per_request": 100, "retry_count": 0}).json()
        project = client.post("/api/projects", json={"name": "Partial"}).json()
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 1, "format": "text", "content": "雨\n青空"}).json()

        def fake_complete(self, system_prompt, user_prompt):
            return {"result": [
                {"line_index": 0, "raw": ["雨"], "pronunciation": [[0, "雨", "あめ"]]},
                {"line_index": 1, "raw": "青空", "pronunciation": [[0, "青空", "あおぞら"]]},
            ]}

        monkeypatch.setattr("app.api.pronunciation.AIClient.complete", fake_complete)
        response = client.post(f"/api/projects/{project['id']}/pronunciation/ai", json={"revision": imported["revision"], "mode": "ai", "profile_id": profile["id"]})
        assert response.status_code == 502
        stored = client.get(f"/api/projects/{project['id']}/document").json()
        assert stored["revision"] == imported["revision"]


def test_ai_pronunciation_validation_failure_does_not_write_local_fallback(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        profile = client.post("/api/settings/ai-profiles", json={"name": "Reading fallback", "base_url": "http://localhost:1234/v1", "model": "demo", "api_key": "secret", "max_chars_per_request": 100, "retry_count": 0}).json()
        project = client.post("/api/projects", json={"name": "Reading fallback"}).json()
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 1, "format": "text", "content": "雨\n青空"}).json()

        def fake_complete(self, system_prompt, user_prompt):
            return {"result": [
                {"line_index": 0, "raw": "雨", "pronunciation": [[0, "雨", "あめ"]]},
                {"line_index": 1, "raw": "青空", "pronunciation": [[1, "青", "あお"]]},
            ]}

        monkeypatch.setattr("app.api.pronunciation.AIClient.complete", fake_complete)
        response = client.post(f"/api/projects/{project['id']}/pronunciation/ai", json={"revision": imported["revision"], "mode": "ai", "profile_id": profile["id"]})
        assert response.status_code == 502
        stored = client.get(f"/api/projects/{project['id']}/document").json()
        assert stored["revision"] == imported["revision"]
        assert all(unit["ruby"] is None for line in stored["document"]["lyrics"]["lines"] for unit in line["units"])


def test_local_pronunciation_updates_only_selected_units_and_preserves_text(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Ruby"}).json()
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 1, "format": "text", "content": "雨が降った\n青空"}).json()
        document = imported["document"]
        first = document["lyrics"]["lines"][0]["units"][0]["id"]
        response = client.post(f"/api/projects/{project['id']}/pronunciation/local", json={"revision": 2, "unit_ids": [first], "mode": "local"})
        assert response.status_code == 200
        updated = response.json()["document"]
        assert "".join(unit["surface"] for unit in updated["lyrics"]["lines"][0]["units"]) == "雨が降った"
        assert updated["lyrics"]["lines"][0]["units"][0]["ruby_source"] == "local"
        assert updated["lyrics"]["lines"][1]["units"][0]["ruby"] is None


def test_ai_protocol_rejects_surface_mismatch_and_splits_units():
    lines = [{"id": "line", "units": [{"id": "unit", "surface": "雨が降った"}]}]
    try:
        validate_ai_result({"result": [{"line_index": 0, "raw": "雨が降つた", "pronunciation": []}]}, lines)
    except PronunciationValidationError:
        pass
    else:
        raise AssertionError("surface mismatch should be rejected")
    result = validate_ai_result({"result": [{"line_index": 0, "raw": "雨が降った", "pronunciation": [[0, "雨", "あめ"], [2, "降", "ふ"]]}]}, lines)
    updated, summary = apply_ai({"lyrics": {"lines": lines}}, result, PronunciationSelection([], []))
    units = updated["lyrics"]["lines"][0]["units"]
    assert "".join(unit["surface"] for unit in units) == "雨が降った"
    assert units[0]["ruby"] == "あめ"
    assert units[2]["ruby"] == "ふ"
    assert summary["applied"] == 2


def test_ai_phrase_reading_is_stored_once_with_ruby_span():
    lines = [{"id": "line", "units": [{"id": "unit", "surface": "昨日", "start_ms": 0, "end_ms": 1000}]}]
    result = validate_ai_result({"result": [{"line_index": 0, "raw": "昨日", "pronunciation": [[0, "昨日", "きのう"]]}]}, lines)
    updated, summary = apply_ai({"lyrics": {"lines": lines}}, result, PronunciationSelection([], []))
    units = updated["lyrics"]["lines"][0]["units"]
    assert [unit["surface"] for unit in units] == ["昨", "日"]
    assert units[0]["ruby"] == "きのう"
    assert units[0]["ruby_span"] == 2
    assert units[1]["ruby"] is None


def test_ai_protocol_uses_full_raw_string_and_derives_end_from_surface():
    lines = [{"id": "line", "units": [{"id": "unit", "surface": "昨日は雨"}]}]
    result = validate_ai_result(
        {"result": [{"line_index": 0, "raw": "昨日は雨", "pronunciation": [[0, "昨日", "きのう"], [3, "雨", "あめ"]]}]},
        lines,
    )
    assert result[0]["raw"] == "昨日は雨"
    assert result[0]["pronunciation"] == [[0, "昨日", "きのう"], [3, "雨", "あめ"]]
    with pytest.raises(PronunciationValidationError):
        validate_ai_result({"result": [{"line_index": 0, "raw": ["昨日は雨"], "pronunciation": []}]}, lines)


def test_ai_apply_splits_unannotated_japanese_with_fa_kara_rules():
    lines = [{"id": "line", "units": [{"id": "unit", "surface": "かなっでABC"}]}]
    result = validate_ai_result({"result": [{"line_index": 0, "raw": "かなっでABC", "pronunciation": []}]}, lines)
    updated, summary = apply_ai({"lyrics": {"lines": lines}}, result, PronunciationSelection([], []))
    assert [unit["surface"] for unit in updated["lyrics"]["lines"][0]["units"]] == ["か", "なっ", "で", "ABC"]
    assert summary["applied"] == 0


def test_profile_response_never_returns_api_key(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        created = client.post("/api/settings/ai-profiles", json={"name": "Local", "base_url": "http://localhost:1234/v1", "model": "demo", "api_key": "super-secret-key"})
        assert created.status_code == 200
        body = created.json()
        assert "encrypted_api_key" not in body
        assert body["has_api_key"] is True
        assert body["api_key_suffix"] == "-key"
        listed = client.get("/api/settings/ai-profiles").json()
        assert "super-secret-key" not in str(listed)


def test_remote_protocol_disconnect_returns_error_without_local_fallback(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        profile = client.post("/api/settings/ai-profiles", json={"name": "Broken", "base_url": "http://127.0.0.1:9/v1", "model": "demo", "api_key": "secret"}).json()
        project = client.post("/api/projects", json={"name": "Fallback"}).json()
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 1, "format": "text", "content": "雨"}).json()

        def disconnect(self, system_prompt, user_prompt):
            raise httpx.RemoteProtocolError("Server disconnected without sending a response")

        monkeypatch.setattr("app.api.pronunciation.AIClient.complete", disconnect)
        response = client.post(f"/api/projects/{project['id']}/pronunciation/ai", json={"revision": imported["revision"], "mode": "ai", "profile_id": profile["id"]})
        assert response.status_code == 502
        stored = client.get(f"/api/projects/{project['id']}/document").json()
        assert stored["revision"] == imported["revision"]
        assert stored["document"]["lyrics"]["lines"][0]["units"][0]["ruby"] is None


def test_full_analysis_stops_when_ai_pronunciation_fails(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        profile = client.post(
            "/api/settings/ai-profiles",
            json={"name": "Broken full analysis", "base_url": "http://127.0.0.1:9/v1", "model": "demo", "api_key": "secret", "retry_count": 0},
        ).json()
        client.put("/api/settings", json={"values": {"default_ai_profile_id": profile["id"]}})
        project = client.post("/api/projects", json={"name": "Stop after AI"}).json()
        imported = client.post(
            f"/api/projects/{project['id']}/lyrics/import",
            json={"revision": 1, "format": "text", "content": "雨"},
        ).json()
        document = imported["document"]
        document["lyrics"]["lines"][0]["units"][0]["ruby"] = "あめ"
        document["lyrics"]["lines"][0]["units"][0]["ruby_source"] = "manual"
        document["media"]["video_filename"] = "video.mp4"
        saved = client.put(
            f"/api/projects/{project['id']}/document",
            json={"revision": imported["revision"], "document": document},
        ).json()

        def disconnect(self, system_prompt, user_prompt):
            raise httpx.RemoteProtocolError("AI disconnected")

        monkeypatch.setattr("app.services.pronunciation.AIClient.complete", disconnect)
        pipeline = client.app.state.analysis_runner.pipeline
        pipeline._separate = lambda job_id, project_id, current, revision, payload, values: (current, revision, {})
        pipeline._transcribe = lambda job_id, project_id, current, revision, payload, values: (current, revision, {})
        alignment_started = []
        pipeline._fa_kara = lambda *args: alignment_started.append(True)

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

        assert job["status"] == "FAILED"
        assert "AI 注音请求失败" in job["error_message"]
        assert alignment_started == []
        stored = client.get(f"/api/projects/{project['id']}/document").json()
        assert stored["revision"] == saved["revision"]
        assert stored["document"]["lyrics"]["lines"][0]["units"][0]["ruby"] == "あめ"
