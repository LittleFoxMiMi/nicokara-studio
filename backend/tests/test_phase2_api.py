import array
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.media.waveform import generate_waveform


def test_import_api_persists_lyrics_in_a_new_revision(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "歌词工程"}).json()
        detected = client.post(
            f"/api/projects/{project['id']}/lyrics/detect",
            json={"content": "[00:01.00]春風"},
        ).json()
        assert detected["format"] == "lrc"
        imported = client.post(
            f"/api/projects/{project['id']}/lyrics/import",
            json={"revision": 1, "format": "auto", "content": "[00:01.00]春風\n[00:03.00]青空"},
        )
        assert imported.status_code == 200
        assert imported.json()["revision"] == 2
        reopened = client.get(f"/api/projects/{project['id']}/document").json()
        assert reopened["revision"] == 2
        assert reopened["document"]["project"]["revision"] == 2
        assert reopened["document"]["lyrics"]["lines"][0]["anchor_ms"] == 1000
        assert client.get("/api/projects").json()[0]["status"] == "review"


def test_import_api_rejects_stale_revision(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Conflict"}).json()
        endpoint = f"/api/projects/{project['id']}/lyrics/import"
        payload = {"revision": 1, "format": "text", "content": "first"}
        assert client.post(endpoint, json=payload).status_code == 200
        assert client.post(endpoint, json=payload).status_code == 409


def test_waveform_generation_writes_normalized_peaks(tmp_path, monkeypatch):
    samples = array.array("h", [-32768, -100, 100, 32767]).tobytes()
    monkeypatch.setattr(
        "app.media.waveform.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=samples),
    )
    target = tmp_path / "waveform.json"
    assert generate_waveform(tmp_path / "video.mp4", target, "ffmpeg", peak_count=2)
    waveform = json.loads(target.read_text(encoding="utf-8"))
    assert waveform["peaks"] == [[-1.0, -0.0031], [0.0031, 1.0]]


def test_waveform_endpoint_backfills_phase1_video(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("NICOKARA_DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        project = client.post("/api/projects", json={"name": "Legacy video"}).json()
        directory = tmp_path / "projects" / project["id"]
        directory.mkdir(parents=True)
        (directory / "video.mp4").write_bytes(b"legacy video")

        def fake_generate(video, target, ffmpeg):
            assert video == directory / "video.mp4"
            target.write_text('{"version":1,"sample_rate":8000,"duration_ms":1000,"peaks":[[-0.5,0.5]]}')
            return True

        monkeypatch.setattr("app.api.projects.generate_waveform", fake_generate)
        response = client.get(f"/api/projects/{project['id']}/waveform")
        assert response.status_code == 200
        assert response.json()["peaks"] == [[-0.5, 0.5]]
