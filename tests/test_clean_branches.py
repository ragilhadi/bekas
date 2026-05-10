"""Tests for clean.py branches not yet covered."""

from pathlib import Path
from unittest.mock import patch

import pytest

from bekas.clean import (
    _generic_remove,
    apply_plan,
    typed_confirmation_gate,
    validate_plan_freshness,
)
from bekas.models import Candidate, Confidence, Context, Plan


def test_validate_plan_freshness_path_missing():
    """Path no longer exists → skip silently."""
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle="/nonexistent/path/for/test_validate",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"fingerprint": {"size_bytes": 1024, "mtime": 1}},
    )
    plan = Plan(audit_id="a1", candidates=[c], fingerprints={c.id: c.metadata["fingerprint"]})
    valid, skipped = validate_plan_freshness(plan)
    assert len(valid) == 0
    assert len(skipped) == 0


def test_validate_plan_freshness_stat_error():
    """OSError on stat → skip silently."""
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=5,
        path_or_handle="/some/fake/path.txt",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"fingerprint": {"size_bytes": 5, "mtime": 1}},
    )
    plan = Plan(audit_id="a1", candidates=[c], fingerprints={c.id: c.metadata["fingerprint"]})
    with patch.object(Path, "exists", return_value=True), patch.object(Path, "stat", side_effect=OSError("bad stat")):
        valid, skipped = validate_plan_freshness(plan)
    assert len(valid) == 0
    assert len(skipped) == 0


def test_validate_plan_freshness_no_fingerprint():
    """No fingerprint stored → assume valid but warn."""
    f = Path(__file__)
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"fingerprint": {}},
    )
    plan = Plan(audit_id="a1", candidates=[c], fingerprints={c.id: {}})
    valid, skipped = validate_plan_freshness(plan)
    assert len(valid) == 1
    assert len(skipped) == 0


def test_validate_plan_freshness_newer_mtime(tmp_path):
    """mtime newer → stale."""
    f = tmp_path / "file.txt"
    f.write_text("hello")
    old_mtime = int(f.stat().st_mtime) - 1000
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=5,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"fingerprint": {"size_bytes": 5, "mtime": old_mtime}},
    )
    plan = Plan(audit_id="a1", candidates=[c], fingerprints={c.id: c.metadata["fingerprint"]})
    valid, skipped = validate_plan_freshness(plan)
    assert len(valid) == 0
    assert len(skipped) == 1
    assert "mtime newer" in skipped[0]


def test_validate_plan_freshness_force_stale(tmp_path):
    """force_stale=True includes candidate but logs it as forced."""
    f = tmp_path / "file.txt"
    f.write_text("hello")
    old_mtime = int(f.stat().st_mtime) - 1000
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=5,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"fingerprint": {"size_bytes": 5, "mtime": old_mtime}},
    )
    plan = Plan(audit_id="a1", candidates=[c], fingerprints={c.id: c.metadata["fingerprint"]})
    valid, skipped = validate_plan_freshness(plan, force_stale=True)
    assert len(valid) == 1
    assert len(skipped) == 1
    assert "forced" in skipped[0]


def test_apply_plan_empty_after_validation():
    """If plan becomes empty after validation, return empty RunResult."""
    f = Path("/nonexistent/path/apply_plan_empty")
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"fingerprint": {"size_bytes": 1024, "mtime": 1}},
    )
    plan = Plan(audit_id="a1", candidates=[c], fingerprints={c.id: c.metadata["fingerprint"]})
    ctx = Context(dry_run=False, config={}, verbose=False)
    result = apply_plan(plan, [], ctx, profile_name="default")
    assert result.total_bytes_freed == 0
    assert result.per_candidate == []


def test_apply_plan_non_interactive_without_yes_all():
    """non_interactive without yes_all should call os._exit(2)."""
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle="/tmp/fake",
        confidence=Confidence.SAFE,
        reason="test",
    )
    plan = Plan(audit_id="a1", candidates=[c])
    ctx = Context(dry_run=False, config={}, verbose=False)
    with (
        patch("bekas.clean.os._exit", side_effect=lambda code: exec("raise SystemExit(code)")),
        pytest.raises(SystemExit) as exc_info,
    ):
        apply_plan(plan, [], ctx, non_interactive=True, yes_all=False)
    assert exc_info.value.code == 2


def test_apply_plan_aborted_confirmation():
    """If typed_confirmation_gate returns False, should call os._exit(1)."""
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle="/tmp/fake",
        confidence=Confidence.SAFE,
        reason="test",
    )
    plan = Plan(audit_id="a1", candidates=[c])
    ctx = Context(dry_run=False, config={}, verbose=False)
    with (
        patch("bekas.clean.typed_confirmation_gate", return_value=False),
        patch("bekas.clean.os._exit", side_effect=lambda code: exec("raise SystemExit(code)")),
        pytest.raises(SystemExit) as exc_info,
    ):
        apply_plan(plan, [], ctx)
    assert exc_info.value.code == 1


def test_typed_confirmation_gate_non_interactive():
    """Non-interactive mode returns False (abort)."""
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle="/tmp/fake",
        confidence=Confidence.SAFE,
        reason="test",
    )
    plan = Plan(audit_id="a1", candidates=[c])
    assert typed_confirmation_gate(plan, quarantine_enabled=False, yes_all=False, non_interactive=True) is False


def test_generic_remove_path_not_exists():
    """If path does not exist, return success=False."""
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle="/nonexistent/path/remove_or_quarantine",
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=False, config={}, verbose=False)
    result = _generic_remove(c, ctx, quarantine_enabled=False, run_id="r1")
    assert result.success is False
    assert "does not exist" in result.log.lower()


def test_generic_remove_dry_run(tmp_path):
    """Dry-run returns success=True, bytes_freed=0."""
    f = tmp_path / "existing.txt"
    f.write_text("x")
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1024,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=True, config={}, verbose=False)
    result = _generic_remove(c, ctx, quarantine_enabled=False, run_id="r1")
    assert result.success is True
    assert result.bytes_freed == 0
    assert result.log == "dry-run"


def test_generic_remove_quarantine_exception(tmp_path, monkeypatch):
    """If quarantine raises, return failure."""
    f = tmp_path / "target.txt"
    f.write_text("x")
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=False, config={}, verbose=False)
    with patch("bekas.clean.move_to_quarantine", side_effect=RuntimeError("boom")):
        result = _generic_remove(c, ctx, quarantine_enabled=True, run_id="r1")
    assert result.success is False
    assert "boom" in result.log


def test_generic_remove_delete_dir(tmp_path):
    """Delete a directory candidate."""
    d = tmp_path / "dir_to_delete"
    d.mkdir()
    (d / "file.txt").write_text("x")
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1,
        path_or_handle=str(d),
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=False, config={}, verbose=False)
    result = _generic_remove(c, ctx, quarantine_enabled=False, run_id="r1")
    assert result.success is True
    assert not d.exists()


def test_generic_remove_delete_file_exception(tmp_path):
    """If unlink raises, return failure."""
    f = tmp_path / "readonly.txt"
    f.write_text("x")
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=False, config={}, verbose=False)
    with patch("pathlib.Path.unlink", side_effect=OSError("perm")):
        result = _generic_remove(c, ctx, quarantine_enabled=False, run_id="r1")
    assert result.success is False
    assert "perm" in result.log


def test_generic_remove_delete_dir_exception(tmp_path):
    """If shutil.rmtree raises, return failure."""
    d = tmp_path / "dir_to_delete_fail"
    d.mkdir()
    (d / "file.txt").write_text("x")
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=1,
        path_or_handle=str(d),
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=False, config={}, verbose=False)
    import shutil

    with patch.object(shutil, "rmtree", side_effect=OSError("perm")):
        result = _generic_remove(c, ctx, quarantine_enabled=False, run_id="r1")
    assert result.success is False
    assert "perm" in result.log


def test_apply_plan_verbose_skipped(capsys, tmp_path):
    """Verbose mode prints skipped reasons."""
    f = tmp_path / "file.txt"
    f.write_text("hello")
    old_mtime = int(f.stat().st_mtime) - 1000
    c = Candidate(
        id="x:1",
        category="test.item",
        size_bytes=5,
        path_or_handle=str(f),
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"fingerprint": {"size_bytes": 5, "mtime": old_mtime}},
    )
    plan = Plan(audit_id="a1", candidates=[c], fingerprints={c.id: c.metadata["fingerprint"]})
    ctx = Context(dry_run=False, config={}, verbose=True)
    result = apply_plan(plan, [], ctx, non_interactive=True, yes_all=True)
    captured = capsys.readouterr()
    assert "plan validation" in captured.out
