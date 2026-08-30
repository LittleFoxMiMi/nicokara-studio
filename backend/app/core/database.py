from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'blank', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS project_revisions (project_id TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id, revision), FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  api_format TEXT NOT NULL DEFAULT 'openai_chat',
  base_url TEXT NOT NULL,
  model TEXT NOT NULL,
  temperature REAL NOT NULL DEFAULT 0.2,
  max_tokens INTEGER NOT NULL DEFAULT 2000,
  timeout_seconds REAL NOT NULL DEFAULT 45,
  max_chars_per_request INTEGER NOT NULL DEFAULT 1200,
  retry_count INTEGER NOT NULL DEFAULT 2,
  thinking_effort TEXT NOT NULL DEFAULT 'off',
  thinking_enabled INTEGER NOT NULL DEFAULT 0,
  custom_payload TEXT NOT NULL DEFAULT '{}',
  encrypted_api_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prompt_presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  system_prompt TEXT NOT NULL,
  user_template TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  progress REAL NOT NULL DEFAULT 0,
  stage TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  input_revision INTEGER NOT NULL,
  output_revision INTEGER,
  request_json TEXT NOT NULL,
  result_json TEXT,
  error_code TEXT,
  error_message TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_project_created ON jobs(project_id, created_at DESC);
"""
def now() -> str: return datetime.now(UTC).isoformat()

class Database:
    def __init__(self, path: Path): self.path = path
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30); c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON"); c.execute("PRAGMA busy_timeout = 5000")
        try:
            yield c; c.commit()
        finally: c.close()
    def initialize(self) -> None:
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            # Existing Phase 4 databases predate the selector column.
            columns = {row[1] for row in c.execute("PRAGMA table_info(ai_profiles)")}
            if "thinking_effort" not in columns:
                c.execute("ALTER TABLE ai_profiles ADD COLUMN thinking_effort TEXT NOT NULL DEFAULT 'off'")
                c.execute("UPDATE ai_profiles SET thinking_effort='low' WHERE thinking_enabled=1")
            if "max_chars_per_request" not in columns:
                c.execute("ALTER TABLE ai_profiles ADD COLUMN max_chars_per_request INTEGER NOT NULL DEFAULT 1200")
            if "retry_count" not in columns:
                c.execute("ALTER TABLE ai_profiles ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 2")
    def create_project(self, project_id: str, name: str, document: dict) -> dict:
        t=now()
        with self.connect() as c:
            c.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)", (project_id,name,t,t))
            c.execute("INSERT INTO project_revisions VALUES(?,?,?,?)", (project_id,1,json.dumps(document,ensure_ascii=False),t))
        return self.get_project(project_id)  # type: ignore[return-value]
    def get_project(self, project_id: str, include_deleted: bool=False) -> dict|None:
        with self.connect() as c:
            row=c.execute("SELECT * FROM projects WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL"),(project_id,)).fetchone()
            if not row: return None
            result=dict(row); result["revision"]=c.execute("SELECT MAX(revision) FROM project_revisions WHERE project_id=?",(project_id,)).fetchone()[0] or 0
            return result
    def list_projects(self, include_deleted=False) -> list[dict]:
        with self.connect() as c:
            rows=c.execute("SELECT * FROM projects WHERE deleted_at IS NULL OR ? ORDER BY updated_at DESC",(1 if include_deleted else 0,)).fetchall()
            return [dict(r) | {"revision": c.execute("SELECT MAX(revision) FROM project_revisions WHERE project_id=?",(r["id"],)).fetchone()[0] or 0} for r in rows]
    def document(self, project_id: str, revision: int|None=None) -> dict|None:
        with self.connect() as c:
            q="SELECT document FROM project_revisions WHERE project_id=?"; args=[project_id]
            if revision is not None: q += " AND revision=?"; args.append(revision)
            q += " ORDER BY revision DESC LIMIT 1"; row=c.execute(q,args).fetchone()
            return json.loads(row[0]) if row else None
    def save_document(self, project_id: str, document: dict, expected: int) -> dict:
        t=now()
        with self.connect() as c:
            current=c.execute("SELECT MAX(revision) FROM project_revisions WHERE project_id=?",(project_id,)).fetchone()[0] or 0
            if current != expected: raise ValueError(f"revision_conflict:{current}")
            rev=current+1
            document["project"]["revision"] = rev
            has_lyrics = bool(document.get("lyrics", {}).get("lines"))
            has_media = bool(document.get("media", {}).get("video_filename"))
            status = "review" if has_lyrics else "media_ready" if has_media else "blank"
            c.execute("INSERT INTO project_revisions VALUES(?,?,?,?)",(project_id,rev,json.dumps(document,ensure_ascii=False),t))
            c.execute("UPDATE projects SET name=?,title=?,artist=?,status=?,updated_at=? WHERE id=?",(document["project"]["name"],document["project"].get("title", ""),document["project"].get("artist", ""),status,t,project_id))
        return {"revision":rev,"document":document}
    def delete_project(self, project_id: str) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM jobs WHERE project_id=?", (project_id,))
            c.execute("DELETE FROM project_revisions WHERE project_id=?", (project_id,))
            c.execute("DELETE FROM projects WHERE id=?", (project_id,))
    def settings(self) -> dict:
        with self.connect() as c: return {r["key"]:json.loads(r["value"]) for r in c.execute("SELECT key,value FROM app_settings")}
    def save_settings(self, values: dict) -> dict:
        t=now()
        with self.connect() as c:
            for k,v in values.items(): c.execute("INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(k,json.dumps(v,ensure_ascii=False),t))
        return self.settings()
    def create_job(self, job_id: str, project_id: str, job_type: str, input_revision: int, payload: dict) -> dict:
        t = now()
        with self.connect() as c:
            c.execute(
                "INSERT INTO jobs(id,project_id,type,status,input_revision,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (job_id, project_id, job_type, "QUEUED", input_revision, json.dumps(payload, ensure_ascii=False), t, t),
            )
        return self.get_job(job_id)  # type: ignore[return-value]
    def get_job(self, job_id: str) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._job_dict(row) if row else None
    def list_jobs(self, project_id: str | None = None, limit: int = 50) -> list[dict]:
        with self.connect() as c:
            if project_id:
                rows = c.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
            else:
                rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [self._job_dict(row) for row in rows]
    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        raw_result = result.pop("result_json")
        result["result"] = json.loads(raw_result) if raw_result else None
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result
    def update_job(self, job_id: str, **changes) -> dict:
        allowed = {"status", "progress", "stage", "message", "output_revision", "result_json", "error_code", "error_message", "cancel_requested", "started_at", "completed_at"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if "result_json" in values and not isinstance(values["result_json"], str):
            values["result_json"] = json.dumps(values["result_json"], ensure_ascii=False)
        values["updated_at"] = now()
        with self.connect() as c:
            c.execute(
                f"UPDATE jobs SET {','.join(f'{key}=?' for key in values)} WHERE id=?",
                (*values.values(), job_id),
            )
        job = self.get_job(job_id)
        if not job: raise ValueError("job_not_found")
        return job
    def mark_interrupted_jobs(self) -> None:
        t = now()
        with self.connect() as c:
            c.execute(
                "UPDATE jobs SET status='FAILED',error_code='service_restarted',error_message='服务重启中断了任务，可手动重试',completed_at=?,updated_at=? WHERE status IN ('QUEUED','PREPARING','RUNNING')",
                (t, t),
            )

    def list_ai_profiles(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM ai_profiles ORDER BY updated_at DESC").fetchall()
            return [self._profile_dict(row) for row in rows]

    def get_ai_profile(self, profile_id: str) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM ai_profiles WHERE id=?", (profile_id,)).fetchone()
            return self._profile_dict(row) if row else None

    @staticmethod
    def _profile_dict(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["thinking_enabled"] = bool(result["thinking_enabled"])
        result["thinking_effort"] = result.get("thinking_effort") or ("low" if result["thinking_enabled"] else "off")
        result["custom_payload"] = json.loads(result.pop("custom_payload") or "{}")
        result.pop("encrypted_api_key", None)
        return result

    def get_ai_profile_secret(self, profile_id: str) -> str | None:
        with self.connect() as c:
            row = c.execute("SELECT encrypted_api_key FROM ai_profiles WHERE id=?", (profile_id,)).fetchone()
            return row[0] if row else None

    def save_ai_profile(self, profile: dict, encrypted_api_key: str | None = None) -> dict:
        t = now()
        with self.connect() as c:
            c.execute(
                """INSERT INTO ai_profiles
                (id,name,api_format,base_url,model,temperature,max_tokens,timeout_seconds,max_chars_per_request,retry_count,thinking_effort,thinking_enabled,custom_payload,encrypted_api_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,api_format=excluded.api_format,base_url=excluded.base_url,
                model=excluded.model,temperature=excluded.temperature,max_tokens=excluded.max_tokens,timeout_seconds=excluded.timeout_seconds,
                max_chars_per_request=excluded.max_chars_per_request,retry_count=excluded.retry_count,
                thinking_effort=excluded.thinking_effort,thinking_enabled=excluded.thinking_enabled,custom_payload=excluded.custom_payload,
                encrypted_api_key=COALESCE(excluded.encrypted_api_key,ai_profiles.encrypted_api_key),updated_at=excluded.updated_at""",
                (profile["id"], profile["name"], profile["api_format"], profile["base_url"], profile["model"],
                 profile["temperature"], profile["max_tokens"], profile["timeout_seconds"], profile.get("max_chars_per_request", 1200), profile.get("retry_count", 2), profile.get("thinking_effort") or ("low" if profile.get("thinking_enabled") else "off"), int(profile.get("thinking_enabled", False)),
                 json.dumps(profile.get("custom_payload", {}), ensure_ascii=False), encrypted_api_key,
                 profile.get("created_at") or t, t),
            )
        return self.get_ai_profile(profile["id"])  # type: ignore[return-value]

    def delete_ai_profile(self, profile_id: str) -> bool:
        with self.connect() as c:
            result = c.execute("DELETE FROM ai_profiles WHERE id=?", (profile_id,))
            return result.rowcount > 0

    def clear_ai_profile_key(self, profile_id: str) -> dict | None:
        with self.connect() as c:
            c.execute("UPDATE ai_profiles SET encrypted_api_key=NULL,updated_at=? WHERE id=?", (now(), profile_id))
        return self.get_ai_profile(profile_id)

    def list_prompt_presets(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM prompt_presets ORDER BY updated_at DESC").fetchall()
            return [self._prompt_dict(row) for row in rows]

    def get_prompt_preset(self, preset_id: str) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM prompt_presets WHERE id=?", (preset_id,)).fetchone()
            return self._prompt_dict(row) if row else None

    @staticmethod
    def _prompt_dict(row: sqlite3.Row) -> dict:
        return dict(row)

    def save_prompt_preset(self, preset: dict) -> dict:
        t = now()
        with self.connect() as c:
            c.execute(
                """INSERT INTO prompt_presets(id,name,system_prompt,user_template,created_at,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,system_prompt=excluded.system_prompt,
                user_template=excluded.user_template,updated_at=excluded.updated_at""",
                (preset["id"], preset["name"], preset["system_prompt"], preset["user_template"], preset.get("created_at", t), t),
            )
        return self.get_prompt_preset(preset["id"])  # type: ignore[return-value]

    def delete_prompt_preset(self, preset_id: str) -> bool:
        with self.connect() as c:
            result = c.execute("DELETE FROM prompt_presets WHERE id=?", (preset_id,))
            return result.rowcount > 0
