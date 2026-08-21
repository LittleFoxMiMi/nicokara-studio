from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import get_settings
def test_project_revision_persists(tmp_path, monkeypatch):
    get_settings.cache_clear(); monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path)); client = TestClient(create_app())
    created = client.post("/api/projects", json={"name": "Demo"}).json(); assert created["revision"] == 1
    doc = client.get(f"/api/projects/{created['id']}/document").json(); assert doc["document"]["project"]["name"] == "Demo"
    saved = doc["document"]; saved["project"]["title"] = "Song"; response = client.put(f"/api/projects/{created['id']}/document", json={"revision": 1, "document": saved}); assert response.status_code == 200
    assert client.get("/api/projects").json()[0]["revision"] == 2
