"""Regression tests: undo is idempotent, serialised, and quarantine moves never leave half-copies."""

from __future__ import annotations

import errno
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from bekas.cli import cli
from bekas.database import add_quarantine, get_run, list_quarantine, log_run
from bekas.locking import ProcessLock
from bekas.models import Candidate, Confidence, RemovalResult, RunResult
from bekas.quarantine import (
    PartialMoveError,
    _safe_move,
    move_to_quarantine,
    purge_old_quarantine,
    restore_from_quarantine,
)


@pytest.fixture
def runner():
    return CliRunner()


def _quarantined_run(tmp_path: Path, name: str = "victim.txt", run_id: str = "run_t1") -> tuple[Path, str]:
    src = tmp_path / name
    src.write_text("precious")
    dest = move_to_quarantine(run_id, src, "fake.test", 8, {})
    cand = Candidate(
        id="c:1",
        category="fake.test",
        size_bytes=8,
        path_or_handle=str(src),
        confidence=Confidence.SAFE,
        reason="r",
    )
    log_run(
        RunResult(
            run_id=run_id,
            audit_id="a",
            per_candidate=[(cand, RemovalResult(success=True, bytes_freed=8, undo_token=str(dest), log="quarantined"))],
            total_bytes_freed=8,
        )
    )
    return src, run_id


# ── idempotent undo ────────────────────────────────────────────


def test_second_undo_reports_already_restored(runner, tmp_path):
    src, run_id = _quarantined_run(tmp_path)

    first = runner.invoke(cli, ["undo", run_id])
    assert first.exit_code == 0, first.output
    assert "Restored 1 item(s). Failed: 0." in first.output
    assert src.read_text() == "precious"

    second = runner.invoke(cli, ["undo", run_id])
    assert second.exit_code == 0, second.output
    assert "Restored 0 item(s). Failed: 0." in second.output
    assert "1 item(s) were already restored" in second.output
    assert "Failed to restore" not in second.output


def test_undo_progress_is_persisted(tmp_path):
    _, run_id = _quarantined_run(tmp_path)
    CliRunner().invoke(cli, ["undo", run_id])
    entries = json.loads(get_run(run_id)["results_json"])
    assert entries[0]["restored_at"]


def test_undo_exits_nonzero_when_an_item_cannot_be_restored(runner, tmp_path):
    src, run_id = _quarantined_run(tmp_path)
    for row in list_quarantine():
        shutil.rmtree(row["quarantine_path"]) if Path(row["quarantine_path"]).is_dir() else Path(
            row["quarantine_path"]
        ).unlink()
    result = runner.invoke(cli, ["undo", run_id])
    assert result.exit_code == 1
    assert "Quarantined data is missing" in result.output
    assert not src.exists()


def test_restore_refuses_when_quarantined_data_is_gone(tmp_path):
    src = tmp_path / "f.txt"
    src.write_text("x")
    dest = move_to_quarantine("r", src, "c", 1, {})
    dest.unlink()
    with pytest.raises(FileNotFoundError, match="missing"):
        restore_from_quarantine(str(dest))


# ── locking ────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [["undo", "run_t1"], ["quarantine", "restore", "q_x"], ["quarantine", "purge"]])
def test_mutating_commands_fail_fast_while_a_clean_holds_the_lock(runner, tmp_path, args):
    _quarantined_run(tmp_path)  # gives `undo run_t1` a real run to act on
    held = ProcessLock()
    held.acquire()
    try:
        # A second lock instance in the same process contends exactly like another process.
        result = runner.invoke(cli, args)
    finally:
        held.release()
    assert result.exit_code == 3
    assert "Another bekas process is running" in result.output


def test_read_only_quarantine_list_ignores_the_lock(runner):
    held = ProcessLock()
    held.acquire()
    try:
        result = runner.invoke(cli, ["quarantine", "list"])
    finally:
        held.release()
    assert result.exit_code == 0


# ── exit codes ─────────────────────────────────────────────────


def test_quarantine_restore_exits_nonzero_on_failure(runner):
    result = runner.invoke(cli, ["quarantine", "restore", "q_does_not_exist"])
    assert result.exit_code == 1
    assert "Restore failed" in result.output


def test_quarantine_restore_succeeds(runner, tmp_path):
    src, _ = _quarantined_run(tmp_path)
    qid = list_quarantine()[0]["quarantine_id"]
    result = runner.invoke(cli, ["quarantine", "restore", qid])
    assert result.exit_code == 0
    assert src.read_text() == "precious"


# ── moves across devices ───────────────────────────────────────


def _force_cross_device(monkeypatch):
    def fake_rename(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr("bekas.quarantine.os.rename", fake_rename)


def test_cross_device_move_copies_then_removes_original(monkeypatch, tmp_path):
    _force_cross_device(monkeypatch)
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("hi")
    dst = tmp_path / "moved"
    _safe_move(src, dst)
    assert not src.exists()
    assert (dst / "sub" / "f.txt").read_text() == "hi"


def test_failed_copy_removes_partial_destination_and_keeps_original(monkeypatch, tmp_path):
    _force_cross_device(monkeypatch)
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f.txt").write_text("hi")
    dst = tmp_path / "moved"

    def broken_copytree(s, d, **kw):
        Path(d).mkdir()
        (Path(d) / "partial").write_text("half")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("bekas.quarantine.shutil.copytree", broken_copytree)
    with pytest.raises(OSError, match="No space"):
        _safe_move(src, dst)
    assert not dst.exists()
    assert (src / "f.txt").read_text() == "hi"


def test_failed_original_removal_is_reported_and_copy_is_kept(monkeypatch, tmp_path):
    _force_cross_device(monkeypatch)
    src = tmp_path / "f.txt"
    src.write_text("data")
    dst = tmp_path / "moved.txt"
    real_unlink = Path.unlink

    def fail_for_src(self, *a, **k):
        if self == src:
            raise PermissionError(errno.EACCES, "denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", fail_for_src)
    with pytest.raises(PartialMoveError) as exc:
        _safe_move(src, dst)
    assert exc.value.dest == dst
    assert dst.read_text() == "data"


def test_partial_move_is_still_recorded_so_it_can_be_restored(monkeypatch, tmp_path):
    _force_cross_device(monkeypatch)
    src = tmp_path / "f.txt"
    src.write_text("data")
    real_unlink = Path.unlink

    def fail_for_src(self, *a, **k):
        if self == src:
            raise PermissionError(errno.EACCES, "denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", fail_for_src)
    with pytest.raises(PartialMoveError):
        move_to_quarantine("r", src, "c", 4, {})
    assert [r["original_path"] for r in list_quarantine()] == [str(src)]


def test_item_is_moved_back_when_the_database_write_fails(tmp_path):
    from bekas.config import quarantine_dir

    src = tmp_path / "f.txt"
    src.write_text("data")
    with (
        patch("bekas.quarantine.add_quarantine", side_effect=RuntimeError("db locked")),
        pytest.raises(RuntimeError, match="db locked"),
    ):
        move_to_quarantine("r", src, "c", 4, {})
    assert src.read_text() == "data"
    assert list(quarantine_dir().rglob("f.txt")) == []


# ── purge ──────────────────────────────────────────────────────


def _row(tmp_path: Path, name: str, ts: str) -> Path:
    f = tmp_path / name
    f.write_text("x")
    qid = add_quarantine("r", "c", str(tmp_path / ("orig_" + name)), str(f), 1, {})
    import sqlite3

    from bekas.config import runs_db_path

    conn = sqlite3.connect(str(runs_db_path()))
    conn.execute("UPDATE quarantine SET timestamp = ? WHERE quarantine_id = ?", (ts, qid))
    conn.commit()
    conn.close()
    return f


def test_purge_handles_naive_timestamps(tmp_path):
    old_naive = (datetime.now(UTC) - timedelta(days=90)).replace(tzinfo=None).isoformat()
    f = _row(tmp_path, "naive.bin", old_naive)
    removed, _ = purge_old_quarantine(retention_days=30)
    assert removed == 1
    assert not f.exists()


def test_purge_skips_malformed_timestamp_and_continues(tmp_path):
    bad = _row(tmp_path, "bad.bin", "not-a-date")
    good = _row(tmp_path, "good.bin", (datetime.now(UTC) - timedelta(days=90)).isoformat())
    removed, _ = purge_old_quarantine(retention_days=30)
    assert removed == 1
    assert bad.exists()  # never delete what we cannot date
    assert not good.exists()


def test_purge_continues_after_a_failing_row(tmp_path, monkeypatch):
    old = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    first = _row(tmp_path, "a.bin", old)
    second = _row(tmp_path, "b.bin", old)
    real_unlink = Path.unlink

    def flaky(self, *a, **k):
        if self.name == "a.bin":
            raise PermissionError("locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky)
    removed, _ = purge_old_quarantine(retention_days=30)
    assert removed == 1
    assert first.exists()
    assert not second.exists()
    assert os.path.exists(first)
