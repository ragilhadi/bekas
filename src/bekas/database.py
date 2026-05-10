"""SQLite undo log and persistence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bekas.config import runs_db_path
from bekas.models import Candidate, Plan, RunResult


def _init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Initialize the SQLite database with required tables.

    Creates the ``runs``, ``quarantine``, and ``audit_cache`` tables if they
    do not exist.

    Args:
        db_path: Optional custom database path. Defaults to ``runs_db_path()``.

    Returns:
        Open SQLite connection with ``row_factory`` set to ``sqlite3.Row``.
    """
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_cache (
            plugin TEXT NOT NULL,
            path TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            candidate_json TEXT NOT NULL,
            cached_at INTEGER NOT NULL,
            PRIMARY KEY (plugin, path)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_cache_cached_at ON audit_cache(cached_at)
        """
    )
    conn.commit()
    return conn


def log_run(result: RunResult, db_path: Path | None = None) -> None:
    """Persist a RunResult into the database.

    Args:
        result: The run result to log.
        db_path: Optional custom database path.
    """
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
    """List all recorded runs ordered by timestamp descending.

    Args:
        db_path: Optional custom database path.

    Returns:
        List of run dictionaries.
    """
    conn = _init_db(db_path)
    rows = conn.execute(
        "SELECT run_id, timestamp, audit_id, total_bytes_freed FROM runs ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_run(run_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Retrieve a single run by its identifier.

    Args:
        run_id: Run identifier to look up.
        db_path: Optional custom database path.

    Returns:
        Run dictionary, or None if not found.
    """
    conn = _init_db(db_path)
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def delete_run(run_id: str, db_path: Path | None = None) -> None:
    """Delete a run record from the database.

    Args:
        run_id: Run identifier to delete.
        db_path: Optional custom database path.
    """
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
    """Add a quarantine entry to the database.

    Args:
        run_id: Identifier of the run that triggered quarantine.
        category: Candidate category.
        original_path: Original filesystem path.
        quarantine_path: Path where the item was quarantined.
        size_bytes: Size of the quarantined item in bytes.
        metadata: Optional metadata dictionary.
        db_path: Optional custom database path.

    Returns:
        The generated quarantine identifier.
    """
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
    """List all quarantine entries ordered by timestamp descending.

    Args:
        db_path: Optional custom database path.

    Returns:
        List of quarantine dictionaries.
    """
    conn = _init_db(db_path)
    rows = conn.execute("SELECT * FROM quarantine ORDER BY timestamp DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_quarantine_item(qid: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Retrieve a single quarantine entry by its identifier.

    Args:
        qid: Quarantine identifier.
        db_path: Optional custom database path.

    Returns:
        Quarantine dictionary, or None if not found.
    """
    conn = _init_db(db_path)
    row = conn.execute("SELECT * FROM quarantine WHERE quarantine_id = ?", (qid,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def remove_quarantine_entry(qid: str, db_path: Path | None = None) -> None:
    """Remove a quarantine record from the database.

    Args:
        qid: Quarantine identifier to delete.
        db_path: Optional custom database path.
    """
    conn = _init_db(db_path)
    conn.execute("DELETE FROM quarantine WHERE quarantine_id = ?", (qid,))
    conn.commit()
    conn.close()


def get_audit_cache(
    plugin: str,
    path: str,
    db_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Retrieve a cached audit entry if it exists.

    Args:
        plugin: Plugin name.
        path: Candidate path.
        db_path: Optional custom database path (ignored if ``conn`` provided).
        conn: Optional existing SQLite connection to reuse.

    Returns:
        Cache dict with candidate_json and fingerprint, or None.
    """
    should_close = conn is None
    if conn is None:
        conn = _init_db(db_path)
    row = conn.execute(
        "SELECT fingerprint, candidate_json FROM audit_cache WHERE plugin = ? AND path = ?",
        (plugin, path),
    ).fetchone()
    if should_close:
        conn.close()
    if not row:
        return None
    return dict(row)


def set_audit_cache(
    plugin: str,
    path: str,
    fingerprint: str,
    candidate: Candidate,
    db_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Store or update an audit cache entry.

    Args:
        plugin: Plugin name.
        path: Candidate path.
        fingerprint: Computed fingerprint (e.g. size+mtime hash).
        candidate: Candidate to serialize.
        db_path: Optional custom database path (ignored if ``conn`` provided).
        conn: Optional existing SQLite connection to reuse.
    """
    should_close = conn is None
    if conn is None:
        conn = _init_db(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO audit_cache (plugin, path, fingerprint, candidate_json, cached_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (plugin, path, fingerprint, candidate.model_dump_json(), int(datetime.now(UTC).timestamp())),
    )
    if should_close:
        conn.commit()
        conn.close()


def clear_audit_cache(db_path: Path | None = None) -> None:
    """Delete all audit cache entries.

    Args:
        db_path: Optional custom database path.
    """
    conn = _init_db(db_path)
    conn.execute("DELETE FROM audit_cache")
    conn.commit()
    conn.close()


def prune_audit_cache(max_age_hours: int = 24, db_path: Path | None = None) -> int:
    """Remove audit cache entries older than the specified TTL.

    Args:
        max_age_hours: Maximum age in hours. Defaults to 24.
        db_path: Optional custom database path.

    Returns:
        Number of rows deleted.
    """
    cutoff = int((datetime.now(UTC) - timedelta(hours=max_age_hours)).timestamp())
    conn = _init_db(db_path)
    cur = conn.execute("DELETE FROM audit_cache WHERE cached_at < ?", (cutoff,))
    conn.commit()
    conn.close()
    return cur.rowcount
