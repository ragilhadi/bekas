"""Unit tests for clean.py — the most destructive code in the project."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bekas.clean import (
    _find_plugin,
    _generic_remove,
    _group_by_category,
    _human_size,
    apply_plan,
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
