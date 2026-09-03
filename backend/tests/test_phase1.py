from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import get_settings
from app.core.defaults import DEFAULT_SUBTITLE_STYLE
def test_project_revision_persists(tmp_path, monkeypatch):
    get_settings.cache_clear(); monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        created = client.post("/api/projects", json={"name": "Demo"}).json(); assert created["revision"] == 1
        doc = client.get(f"/api/projects/{created['id']}/document").json(); assert doc["document"]["project"]["name"] == "Demo"
        saved = doc["document"]; saved["project"]["title"] = "Song"; response = client.put(f"/api/projects/{created['id']}/document", json={"revision": 1, "document": saved}); assert response.status_code == 200
        assert client.get("/api/projects").json()[0]["revision"] == 2


def test_current_settings_and_kohakutou_style_are_new_install_defaults(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        settings = client.get("/api/settings").json()
        assert settings["separator_vocals_model"] == "UVR-MDX-NET-Voc_FT.onnx"
        assert settings["separator_instrumental_model"] == "UVR_MDXNET_KARA_2.onnx"
        assert settings["whisper_model"] == "large-v3"
        assert settings["stable_ts_segment_padding_seconds"] == 0
        assert settings["export_mp4_crf"] == 18
        assert settings["export_h264_preset"] == "slow"
        project = client.post("/api/projects", json={"name": "Defaults"}).json()
        document = client.get(f"/api/projects/{project['id']}/document").json()["document"]
        assert document["styles"] == DEFAULT_SUBTITLE_STYLE


def test_project_style_presets_migrate_globally_and_stay_deleted(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Legacy presets"}).json()
        loaded = client.get(f"/api/projects/{project['id']}/document").json()
        document = loaded["document"]
        document["styles"]["presets"] = [{"id": "legacy-style", "name": "我的样式", "style": {"fontSizeMax": 47}}]
        assert client.put(
            f"/api/projects/{project['id']}/document",
            json={"revision": loaded["revision"], "document": document},
        ).status_code == 200

    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        migrated = client.get("/api/settings/subtitle-style-presets").json()
        assert [(item["id"], item["name"]) for item in migrated] == [("legacy-style", "我的样式")]
        saved = client.post(
            "/api/settings/subtitle-style-presets",
            json={"name": "我的样式", "style": {"fontSizeMax": 60, "activeColor": "#c79af6"}},
        ).json()
        assert saved["id"] == "legacy-style"
        assert saved["style"]["fontSizeMax"] == 60
        assert client.delete("/api/settings/subtitle-style-presets/legacy-style").status_code == 204
        assert client.get("/api/settings/subtitle-style-presets").json() == []

    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/api/settings/subtitle-style-presets").json() == []
