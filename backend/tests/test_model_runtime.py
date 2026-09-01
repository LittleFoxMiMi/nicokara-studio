from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.model_runtime import ResidentModelStore


def test_resident_model_store_reuses_one_model_and_switches_backends() -> None:
    store = ResidentModelStore()
    loads: list[str] = []

    first = store.get_or_load("whisper:small", "Whisper small", lambda: loads.append("whisper") or object())
    again = store.get_or_load("whisper:small", "Whisper small", lambda: loads.append("duplicate") or object())
    second = store.get_or_load("fa-kara:mms", "FA-Kara mms", lambda: loads.append("fa-kara") or object())

    assert first is again
    assert second is not first
    assert loads == ["whisper", "fa-kara"]
    assert store.status()["label"] == "FA-Kara mms"
    assert store.release()["released"] is True
    assert store.status()["loaded"] is False


def test_resident_model_api_reports_and_releases(tmp_path, monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        client.app.state.model_store.get_or_load("test:model", "Test model", object)
        assert client.get("/api/models/resident").json()["label"] == "Test model"
        released = client.delete("/api/models/resident")
        assert released.status_code == 200
        assert released.json()["loaded"] is False
    get_settings.cache_clear()
