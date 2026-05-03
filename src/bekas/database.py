"""SQLite undo log and persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bekas.config import runs_db_path
from bekas.models import Plan, RunResult


def _init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or runs_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            audit_id TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            results_json TEXT NOT NULL,
            total_bytes_freed INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quarantine (
            quarantine_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            original_path TEXT NOT NULL,
            quarantine_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT
        )
        """
    )
    conn.commit()
    return conn


def log_run(result: RunResult, db_path: Path | None = None) -> None:
    conn = _init_db(db_path)
    plan = Plan(audit_id=result.audit_id, candidates=[c for c, _ in result.per_candidate])
    results = [
        {
            "candidate_id": c.id,
            "success": r.success,
            "bytes_freed": r.bytes_freed,
            "undo_token": r.undo_token,
            "log": r.log,
        }
        for c, r in result.per_candidate
    ]
    conn.execute(
        "INSERT INTO runs (run_id, timestamp, audit_id, plan_json, results_json, total_bytes_freed) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            result.run_id,
            result.timestamp.isoformat(),
            result.audit_id,
            plan.model_dump_json(),
            json.dumps(results),
            result.total_bytes_freed,
        ),
    )
    conn.commit()
    conn.close()


def list_runs(db_path: Path | None = None) -> list[dict[str, Any]]:
    conn = _init_db(db_path)
    rows = conn.execute(
        "SELECT run_id, timestamp, audit_id, total_bytes_freed FROM runs ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_run(run_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    conn = _init_db(db_path)
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def delete_run(run_id: str, db_path: Path | None = None) -> None:
    conn = _init_db(db_path)
    conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    conn.commit()
    conn.close()


def add_quarantine(
    run_id: str,
    category: str,
    original_path: str,
    quarantine_path: str,
    size_bytes: int,
    metadata: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> str:
    conn = _init_db(db_path)
    qid = f"q_{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO quarantine (quarantine_id, run_id, timestamp, category, "
        "original_path, quarantine_path, size_bytes, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            qid,
            run_id,
            datetime.now(UTC).isoformat(),
            category,
            original_path,
            quarantine_path,
            size_bytes,
            json.dumps(metadata or {}),
        ),
    )
    conn.commit()
    conn.close()
    return qid


def list_quarantine(db_path: Path | None = None) -> list[dict[str, Any]]:
    conn = _init_db(db_path)
    rows = conn.execute("SELECT * FROM quarantine ORDER BY timestamp DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_quarantine_item(qid: str, db_path: Path | None = None) -> dict[str, Any] | None:
    conn = _init_db(db_path)
    row = conn.execute("SELECT * FROM quarantine WHERE quarantine_id = ?", (qid,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def remove_quarantine_entry(qid: str, db_path: Path | None = None) -> None:
    conn = _init_db(db_path)
    conn.execute("DELETE FROM quarantine WHERE quarantine_id = ?", (qid,))
    conn.commit()
    conn.close()
