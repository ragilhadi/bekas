"""Unit tests for clean.py — the most destructive code in the project."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from bekas.clean import (
    _find_plugin,
    _generic_remove,
    _group_by_category,
    _human_size,
    apply_plan,
    typed_confirmation_gate,
    validate_plan_freshness,
)
from bekas.models import Candidate, Confidence, Context, Plan, RemovalResult
from bekas.plugin import Plugin


class FakePlugin(Plugin):
    name = "fake.test"
    description = "fake"

    def discover(self, ctx):
        yield from []

    def remove(self, candidate, ctx):
        return RemovalResult(success=True, bytes_freed=candidate.size_bytes, log="deleted")

    def supports_quarantine(self):
        return True

    def quarantine(self, candidate, ctx, quarantine_dir, run_id=None):
        return RemovalResult(
            success=True, bytes_freed=candidate.size_bytes, undo_token="/quarantine/x", log="quarantined"
        )


def test_find_plugin():
    p1 = FakePlugin()
    assert _find_plugin([p1], "fake.test") is p1
    assert _find_plugin([p1], "nope") is None


def test_group_by_category():
    c1 = Candidate(
        id="a", category="docker.image", size_bytes=0, path_or_handle="x", confidence=Confidence.SAFE, reason="r"
    )
    c2 = Candidate(
        id="b", category="docker.container", size_bytes=0, path_or_handle="y", confidence=Confidence.SAFE, reason="r"
    )
    c3 = Candidate(
        id="c", category="python.venv", size_bytes=0, path_or_handle="z", confidence=Confidence.SAFE, reason="r"
    )
    groups = _group_by_category([c1, c2, c3])
    assert len(groups["docker"]) == 2
    assert len(groups["python"]) == 1


def test_human_size():
    assert _human_size(0) == "0.0 B"
    assert _human_size(1024) == "1.0 KB"
    assert _human_size(1024 * 1024) == "1.0 MB"
    assert _human_size(1024**5) == "1.0 PB"


def test_apply_plan_dry_run():
    c = Candidate(
        id="a",
        category="fake.test",
        size_bytes=100,
        path_or_handle="/tmp/fake",
        confidence=Confidence.SAFE,
        reason="reason",
    )
    plan = Plan(audit_id="ad1", candidates=[c])
    ctx = Context(dry_run=True)
    plugin = FakePlugin()
    result = apply_plan(plan, [plugin], ctx)
    assert result.total_bytes_freed == 0
    assert len(result.per_candidate) == 1
    assert result.per_candidate[0][1].success is True
    assert result.per_candidate[0][1].log == "dry-run"


def test_apply_plan_excluded_skipped(tmp_path: Path):
    # Create a file under /tmp that is NOT excluded
    safe_file = tmp_path / "safe.txt"
    safe_file.write_text("hello")
    c = Candidate(
        id="a",
        category="fake.test",
        size_bytes=5,
        path_or_handle=str(safe_file),
        confidence=Confidence.SAFE,
        reason="r",
    )
    plan = Plan(audit_id="ad1", candidates=[c])
    ctx = Context(dry_run=True)
    result = apply_plan(plan, [FakePlugin()], ctx)
    assert len(result.per_candidate) == 1


def test_generic_remove_quarantine(tmp_path: Path):
    f = tmp_path / "to_remove.txt"
    f.write_text("bye")
    c = Candidate(id="a", category="x", size_bytes=3, path_or_handle=str(f), confidence=Confidence.SAFE, reason="r")
    ctx = Context(dry_run=False)
    with patch("bekas.clean.move_to_quarantine") as mock_move:
        mock_move.return_value = Path("/quarantine/to_remove.txt")
        result = _generic_remove(c, ctx, quarantine_enabled=True, run_id="run_01")
    assert result.success is True
    assert result.undo_token == "/quarantine/to_remove.txt"


def test_generic_remove_delete(tmp_path: Path):
    f = tmp_path / "to_remove.txt"
    f.write_text("bye")
    c = Candidate(id="a", category="x", size_bytes=3, path_or_handle=str(f), confidence=Confidence.SAFE, reason="r")
    ctx = Context(dry_run=False)
    result = _generic_remove(c, ctx, quarantine_enabled=False, run_id="run_01")
    assert result.success is True
    assert not f.exists()


def test_apply_plan_quarantine_counts_bytes():
    """Quarantined items must always count toward total_freed."""
    c = Candidate(
        id="a",
        category="fake.test",
        size_bytes=100,
        path_or_handle="/tmp/fake",
        confidence=Confidence.SAFE,
        reason="reason",
    )
    plan = Plan(audit_id="ad1", candidates=[c])
    ctx = Context(dry_run=False)
    plugin = FakePlugin()
    result = apply_plan(plan, [plugin], ctx, quarantine_enabled=True, yes_all=True)
    assert result.total_bytes_freed == 100  # Bug fix: counts even without undo_token


def test_apply_plan_yes_all_skips_input():
    """With yes_all, per-category input should be skipped."""
    c = Candidate(
        id="a",
        category="fake.test",
        size_bytes=100,
        path_or_handle="/tmp/fake",
        confidence=Confidence.SAFE,
        reason="reason",
    )
    plan = Plan(audit_id="ad1", candidates=[c])
    ctx = Context(dry_run=False)
    plugin = FakePlugin()
    # If yes_all is False and dry_run is False, it would call input() which blocks.
    result = apply_plan(plan, [plugin], ctx, yes_all=True)
    assert result.total_bytes_freed == 100


# ── P1.2 typed confirmation gate ──────────────────────────────


def test_confirmation_gate_yes_all_skips():
    """yes_all=True skips gate."""
    c = Candidate(id="x", category="c", size_bytes=100, path_or_handle="/x", confidence=Confidence.SAFE, reason="")
    plan = Plan(audit_id="ad", candidates=[c])
    assert typed_confirmation_gate(plan, quarantine_enabled=True, yes_all=True) is True


def test_confirmation_gate_non_interactive_refuses():
    """non_interactive without yes_all returns False."""
    c = Candidate(id="x", category="c", size_bytes=100, path_or_handle="/x", confidence=Confidence.SAFE, reason="")
    plan = Plan(audit_id="ad", candidates=[c])
    assert typed_confirmation_gate(plan, quarantine_enabled=True, yes_all=False, non_interactive=True) is False


def test_confirmation_gate_high_risk_requires_token(monkeypatch):
    """Manual-tier triggers token requirement."""
    c = Candidate(id="x", category="c", size_bytes=100, path_or_handle="/x", confidence=Confidence.MANUAL, reason="")
    plan = Plan(audit_id="ad", candidates=[c])

    def _fake_input(prompt=""):
        return "ABCD"

    monkeypatch.setattr("builtins.input", _fake_input)
    import bekas.clean as clean_mod

    orig_token = clean_mod.secrets.token_urlsafe
    clean_mod.secrets.token_urlsafe = lambda n: "ABCD"
    try:
        assert typed_confirmation_gate(plan, quarantine_enabled=True) is True
    finally:
        clean_mod.secrets.token_urlsafe = orig_token


def test_confirmation_gate_normal_requires_yes(monkeypatch):
    """Normal (SAFE-only) plan requires typing 'yes'."""
    c = Candidate(id="x", category="c", size_bytes=100, path_or_handle="/x", confidence=Confidence.SAFE, reason="")
    plan = Plan(audit_id="ad", candidates=[c])
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert typed_confirmation_gate(plan, quarantine_enabled=True) is True
    monkeypatch.setattr("builtins.input", lambda: "nope")
    assert typed_confirmation_gate(plan, quarantine_enabled=True) is False


# ── P1.5 plan re-validation ─────────────────────────────────


def test_validate_freshness_skips_missing():
    """Candidates with missing paths are skipped silently."""
    c = Candidate(
        id="missing",
        category="c",
        size_bytes=100,
        path_or_handle="/does/not/exist/ever/12345",
        confidence=Confidence.SAFE,
        reason="",
    )
    plan = Plan(audit_id="ad", candidates=[c], fingerprints={"missing": {"exists": True, "size_bytes": 100}})
    valid, skipped = validate_plan_freshness(plan)
    assert len(valid) == 0
    assert len(skipped) == 0


def test_validate_freshness_warns_stale_size():
    """Growing size triggers stale detection."""
    import os as os_mod
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"small")
        path = f.name
    try:
        c = Candidate(id="x", category="c", size_bytes=5, path_or_handle=path, confidence=Confidence.SAFE, reason="")
        plan = Plan(audit_id="ad", candidates=[c], fingerprints={"x": {"exists": True, "size_bytes": 5}})
        with open(path, "w") as f:
            f.write("this is much bigger now")
        valid, skipped = validate_plan_freshness(plan)
        assert len(valid) == 0
        assert any("stale" in s.lower() for s in skipped)
    finally:
        os_mod.unlink(path)


def test_validate_freshness_force_stale():
    """force_stale=True includes stale candidates."""
    import os as os_mod
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"small")
        f.flush()
        path = f.name
    try:
        c = Candidate(id="x", category="c", size_bytes=5, path_or_handle=path, confidence=Confidence.SAFE, reason="")
        plan = Plan(audit_id="ad", candidates=[c], fingerprints={"x": {"exists": True, "size_bytes": 5}})
        with open(path, "w") as f:
            f.write("this is much bigger now")
        valid, skipped = validate_plan_freshness(plan, force_stale=True)
        assert len(valid) == 1
        assert any("forced" in s.lower() for s in skipped)
    finally:
        os_mod.unlink(path)


def test_validate_freshness_warns_old_plan():
    """Plan signed > 7 days ago gets a warning."""
    c = Candidate(
        id="x",
        category="c",
        size_bytes=5,
        path_or_handle="/tmp/fake_file_12345_xyz",
        confidence=Confidence.SAFE,
        reason="",
    )
    plan = Plan(audit_id="ad", candidates=[c], signed_at=datetime.now(UTC) - timedelta(days=10))
    valid, skipped = validate_plan_freshness(plan)
    assert any("7 days" in s for s in skipped)
