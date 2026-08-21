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
        with self.connect() as c: c.execute("PRAGMA journal_mode=WAL"); c.executescript(SCHEMA)
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
            rev=current+1; c.execute("INSERT INTO project_revisions VALUES(?,?,?,?)",(project_id,rev,json.dumps(document,ensure_ascii=False),t)); c.execute("UPDATE projects SET name=?,title=?,artist=?,updated_at=? WHERE id=?",(document["project"]["name"],document["project"].get("title", ""),document["project"].get("artist", ""),t,project_id))
        return {"revision":rev,"document":document}
    def delete_project(self, project_id: str) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM project_revisions WHERE project_id=?", (project_id,))
            c.execute("DELETE FROM projects WHERE id=?", (project_id,))
    def settings(self) -> dict:
        with self.connect() as c: return {r["key"]:json.loads(r["value"]) for r in c.execute("SELECT key,value FROM app_settings")}
    def save_settings(self, values: dict) -> dict:
        t=now()
        with self.connect() as c:
            for k,v in values.items(): c.execute("INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(k,json.dumps(v,ensure_ascii=False),t))
        return self.settings()
