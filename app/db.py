"""SQLite 持久化：基线方案与校核快照。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.environ.get("SELECTIVITY_DB", "/workspace/data/selectivity.db")

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            doc_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            label TEXT,
            rules_version TEXT NOT NULL,
            input_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_baseline(doc: dict, name: str | None) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, doc_json, created_at) VALUES (?, ?, ?)",
            (name or "baseline", json.dumps(doc, ensure_ascii=False), _now()),
        )
        return cur.lastrowid


def get_baseline() -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id, name, doc_json, created_at FROM projects ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "name": row["name"],
            "doc": json.loads(row["doc_json"]), "created_at": row["created_at"]}


def save_snapshot(project_id: int | None, label: str | None, rules_version: str,
                  input_doc: dict, result: dict) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (project_id, label, rules_version, input_json, result_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, label, rules_version,
             json.dumps(input_doc, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False), _now()),
        )
        return cur.lastrowid


def get_snapshot(snapshot_id: int) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "project_id": row["project_id"], "label": row["label"],
            "rules_version": row["rules_version"],
            "input": json.loads(row["input_json"]),
            "result": json.loads(row["result_json"]),
            "created_at": row["created_at"]}


def list_snapshots() -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, project_id, label, rules_version, created_at, result_json"
            " FROM snapshots ORDER BY id DESC"
        ).fetchall()
    return [{"id": r["id"], "project_id": r["project_id"], "label": r["label"],
             "rules_version": r["rules_version"], "created_at": r["created_at"],
             "summary": json.loads(r["result_json"])["summary"]} for r in rows]
