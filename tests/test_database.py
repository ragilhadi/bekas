"""Tests for SQLite persistence in database.py."""

from __future__ import annotations

import json
from pathlib import Path

from bekas.database import (
    _init_db,
    add_quarantine,
    delete_run,
    get_run,
    list_quarantine,
    list_runs,
    log_run,
    remove_quarantine_entry,
)
from bekas.models import Candidate, Confidence, Plan, RemovalResult, RunResult


def test_init_db_creates_tables(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = _init_db(db)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {t[0] for t in tables}
    assert "runs" in names
    assert "quarantine" in names
    conn.close()


def test_log_run_and_list(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    c = Candidate(id="a", category="x", size_bytes=10, path_or_handle="/tmp/x", confidence=Confidence.SAFE, reason="r")
    plan = Plan(audit_id="ad1", candidates=[c])
    result = RunResult(
        run_id="run_01",
        audit_id="ad1",
        per_candidate=[(c, RemovalResult(success=True, bytes_freed=10))],
        total_bytes_freed=10,
    )
    log_run(result, db_path=db)
    runs = list_runs(db_path=db)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run_01"


def test_get_run(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    c = Candidate(id="a", category="x", size_bytes=10, path_or_handle="/tmp/x", confidence=Confidence.SAFE, reason="r")
    result = RunResult(
        run_id="run_02",
        audit_id="ad1",
        per_candidate=[(c, RemovalResult(success=True, bytes_freed=10))],
        total_bytes_freed=10,
    )
    log_run(result, db_path=db)
    row = get_run("run_02", db_path=db)
    assert row is not None
    assert row["run_id"] == "run_02"
    # Verify plan_json and results_json are parseable
    plan_data = json.loads(row["plan_json"])
    assert plan_data["audit_id"] == "ad1"
    results = json.loads(row["results_json"])
    assert len(results) == 1
    assert results[0]["success"] is True


def test_delete_run(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    c = Candidate(id="a", category="x", size_bytes=10, path_or_handle="/tmp/x", confidence=Confidence.SAFE, reason="r")
    result = RunResult(
        run_id="run_03",
        audit_id="ad1",
        per_candidate=[(c, RemovalResult(success=True, bytes_freed=10))],
        total_bytes_freed=10,
    )
    log_run(result, db_path=db)
    delete_run("run_03", db_path=db)
    assert get_run("run_03", db_path=db) is None


def test_add_quarantine_and_list(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    qid = add_quarantine("run_01", "test", "/tmp/x", "/quarantine/x", 10, {}, db_path=db)
    items = list_quarantine(db_path=db)
    assert len(items) == 1
    assert items[0]["quarantine_id"] == qid


def test_remove_quarantine_entry(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    qid = add_quarantine("run_01", "test", "/tmp/x", "/quarantine/x", 10, {}, db_path=db)
    remove_quarantine_entry(qid, db_path=db)
    items = list_quarantine(db_path=db)
    assert len(items) == 0
