from __future__ import annotations

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
            return {"result": [{"line_index": line_index, "raw": [text], "pronunciation": []}]}

        monkeypatch.setattr("app.api.pronunciation.AIClient.complete", fake_complete)
        response = client.post(f"/api/projects/{project['id']}/pronunciation/ai", json={"revision": imported["revision"], "mode": "ai", "profile_id": profile["id"]})
        assert response.status_code == 200
        assert response.json()["summary"]["batch_count"] == 2
        assert response.json()["summary"]["retry_count"] == 1
        assert len(calls) == 3


def test_ai_pronunciation_keeps_reading_when_raw_tokenization_is_wrong(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        profile = client.post("/api/settings/ai-profiles", json={"name": "Partial", "base_url": "http://localhost:1234/v1", "model": "demo", "api_key": "secret", "max_chars_per_request": 100, "retry_count": 0}).json()
        project = client.post("/api/projects", json={"name": "Partial"}).json()
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 1, "format": "text", "content": "雨\n青空"}).json()

        def fake_complete(self, system_prompt, user_prompt):
            return {"result": [
                {"line_index": 0, "raw": ["雨"], "pronunciation": [[0, 1, "雨", "あめ"]]},
                {"line_index": 1, "raw": ["青"], "pronunciation": [[0, 2, "青空", "あおぞら"]]},
            ]}

        monkeypatch.setattr("app.api.pronunciation.AIClient.complete", fake_complete)
        response = client.post(f"/api/projects/{project['id']}/pronunciation/ai", json={"revision": imported["revision"], "mode": "ai", "profile_id": profile["id"]})
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["local_fallback_lines"] == 0
        assert body["summary"]["raw_mismatch_lines"] == 1
        units = body["document"]["lyrics"]["lines"]
        assert units[0]["units"][0]["ruby_source"] == "ai"
        assert units[1]["units"][0]["ruby_source"] == "ai"


def test_ai_pronunciation_uses_local_reading_after_retries_are_exhausted(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        profile = client.post("/api/settings/ai-profiles", json={"name": "Reading fallback", "base_url": "http://localhost:1234/v1", "model": "demo", "api_key": "secret", "max_chars_per_request": 100, "retry_count": 0}).json()
        project = client.post("/api/projects", json={"name": "Reading fallback"}).json()
        imported = client.post(f"/api/projects/{project['id']}/lyrics/import", json={"revision": 1, "format": "text", "content": "雨\n青空"}).json()

        def fake_complete(self, system_prompt, user_prompt):
            return {"result": [
                {"line_index": 0, "raw": ["雨"], "pronunciation": [[0, 1, "雨", "あめ"]]},
                {"line_index": 1, "raw": ["青空"], "pronunciation": [[0, 2, "青", "あお"]]},
            ]}

        monkeypatch.setattr("app.api.pronunciation.AIClient.complete", fake_complete)
        response = client.post(f"/api/projects/{project['id']}/pronunciation/ai", json={"revision": imported["revision"], "mode": "ai", "profile_id": profile["id"]})
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["local_fallback_lines"] == 1
        units = body["document"]["lyrics"]["lines"]
        assert units[0]["units"][0]["ruby_source"] == "ai"
        assert units[1]["units"][0]["ruby_source"] == "local_fallback"


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
        validate_ai_result({"result": [{"line_index": 0, "raw": ["雨が降つた"], "pronunciation": []}]}, lines)
    except PronunciationValidationError:
        pass
    else:
        raise AssertionError("surface mismatch should be rejected")
    result = validate_ai_result({"result": [{"line_index": 0, "raw": ["雨が", "降った"], "pronunciation": [[0, 1, "雨", "あめ"], [2, 3, "降", "ふ"]]}]}, lines)
    updated, summary = apply_ai({"lyrics": {"lines": lines}}, result, PronunciationSelection([], []))
    units = updated["lyrics"]["lines"][0]["units"]
    assert "".join(unit["surface"] for unit in units) == "雨が降った"
    assert units[0]["ruby"] == "あめ"
    assert units[2]["ruby"] == "ふ"
    assert summary["applied"] == 2


def test_ai_phrase_reading_is_stored_once_with_ruby_span():
    lines = [{"id": "line", "units": [{"id": "unit", "surface": "昨日", "start_ms": 0, "end_ms": 1000}]}]
    result = validate_ai_result({"result": [{"line_index": 0, "raw": ["昨日"], "pronunciation": [[0, 2, "昨日", "きのう"]]}]}, lines)
    updated, summary = apply_ai({"lyrics": {"lines": lines}}, result, PronunciationSelection([], []))
    units = updated["lyrics"]["lines"][0]["units"]
    assert len(units) == 1
    assert units[0]["ruby"] == "きのう"
    assert units[0]["ruby_span"] == 2
    assert summary["applied"] == 1


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


def test_remote_protocol_disconnect_uses_local_fallback_instead_of_500(tmp_path, monkeypatch):
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
        assert response.status_code == 200
        assert response.json()["summary"]["source"] == "local_fallback"
