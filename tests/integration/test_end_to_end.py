"""End-to-end integration test: audit → plan → clean → undo cycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bekas.clean import apply_plan
from bekas.cli import _audit_to_plan
from bekas.models import (
    AuditReport,
    AuditSummary,
    Candidate,
    Confidence,
    Context,
    Plan,
    PluginReport,
    RemovalResult,
    SystemInfo,
)
from bekas.plugin import Plugin
from bekas.quarantine import move_to_quarantine, restore_from_quarantine
from bekas.runner import run_audit


class FakeFilePlugin(Plugin):
    """A fake plugin that discovers files in a temp directory."""

    name = "fake.files"
    description = "Finds fake files for testing."
    requires_commands = []

    def __init__(self, root: Path):
        self.root = root

    def is_available(self, ctx: Context) -> bool:
        return True

    def discover(self, ctx: Context):
        for entry in self.root.iterdir():
            if entry.is_file():
                yield Candidate(
                    id=f"fake:{entry.name}",
                    category="fake.file",
                    size_bytes=entry.stat().st_size,
                    path_or_handle=str(entry),
                    confidence=Confidence.SAFE,
                    reason="Test file.",
                    metadata={"path": str(entry)},
                )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="gone")
        size = path.stat().st_size
        path.unlink()
        return RemovalResult(success=True, bytes_freed=size, log="deleted")

    def supports_quarantine(self) -> bool:
        return True

    def quarantine(self, candidate, ctx, quarantine_dir, run_id=None):
        path = Path(candidate.path_or_handle)
        size = path.stat().st_size
        dest = move_to_quarantine(run_id or "run_test", path, candidate.category, size, candidate.metadata)
        return RemovalResult(success=True, bytes_freed=size, undo_token=str(dest), log="quarantined")


def _make_report(*candidates: Candidate) -> AuditReport:
    return AuditReport(
        audit_id="audit_01",
        started_at=datetime.now(UTC),
        plugins=[PluginReport(name="fake.files", candidates_found=len(candidates), candidates=list(candidates))],
        summary=AuditSummary(
            total_candidates=len(candidates),
            total_bytes=sum(c.size_bytes for c in candidates),
            by_confidence={"safe": {"count": len(candidates), "bytes": sum(c.size_bytes for c in candidates)}},
        ),
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=10**12),
        duration_ms=100,
    )


class TestEndToEnd:
    def test_audit_plan_clean_undo_with_quarantine(self, tmp_path):
        """Full cycle: audit discovers files, plan is generated, clean quarantines them, undo restores them."""
        # Setup fake home with files
        home = tmp_path / "home"
        home.mkdir()
        f1 = home / "old1.txt"
        f1.write_text("hello world")  # 11 bytes
        f2 = home / "old2.txt"
        f2.write_text("goodbye")  # 7 bytes

        # Use the fake plugin
        plugin = FakeFilePlugin(home)

        # 1. Audit
        ctx = Context(dry_run=True, config={})
        cands = list(plugin.discover(ctx))
        assert len(cands) == 2
        report = _make_report(*cands)

        # 2. Plan (safe-only)
        plan = _audit_to_plan(report, safe_only=True)
        assert len(plan.candidates) == 2
        assert plan.total_bytes() == 18

        # 3. Clean with quarantine - use real quarantine but redirect to tmp
        quarantine_root = tmp_path / "quarantine"
        quarantine_root.mkdir()
        db_path = tmp_path / "bekas.sqlite"
        with (
            patch("bekas.quarantine.quarantine_dir", return_value=quarantine_root),
            patch("bekas.database.runs_db_path", return_value=db_path),
        ):
            ctx_apply = Context(dry_run=False, config={"quarantine_enabled": True}, verbose=False)
            result = apply_plan(plan, [plugin], ctx_apply, yes_all=True, quarantine_enabled=True)

        # Files should be gone from home
        assert not f1.exists()
        assert not f2.exists()
        assert result.total_bytes_freed == 18
        assert len(result.per_candidate) == 2
        assert all(r.success for _, r in result.per_candidate)

        # 4. Undo via quarantine restore
        with (
            patch("bekas.quarantine.quarantine_dir", return_value=quarantine_root),
            patch("bekas.database.runs_db_path", return_value=db_path),
        ):
            for _c, r in result.per_candidate:
                if r.undo_token:
                    restored = restore_from_quarantine(r.undo_token)
                    assert restored.exists()

        # Files should be back
        assert f1.exists()
        assert f2.exists()

    def test_audit_plan_dry_run_no_changes(self, tmp_path):
        """Dry-run clean should not modify the filesystem."""
        home = tmp_path / "home"
        home.mkdir()
        f1 = home / "file.txt"
        f1.write_text("data")

        plugin = FakeFilePlugin(home)
        cands = list(plugin.discover(Context(dry_run=True, config={})))
        report = _make_report(*cands)
        plan = _audit_to_plan(report)

        ctx = Context(dry_run=True, config={})
        result = apply_plan(plan, [plugin], ctx, yes_all=True)

        # File should still exist
        assert f1.exists()
        assert result.total_bytes_freed == 0
        # Dry-run results show success=True but bytes_freed=0
        for _, r in result.per_candidate:
            assert r.success is True
            assert r.bytes_freed == 0
            assert "dry-run" in r.log

    def test_plan_with_review_items(self, tmp_path):
        """Plan should include REVIEW items only when requested."""
        home = tmp_path / "home"
        home.mkdir()
        safe = Candidate(
            id="fake:safe.txt",
            category="fake.file",
            size_bytes=5,
            path_or_handle=str(home / "safe.txt"),
            confidence=Confidence.SAFE,
            reason="safe",
        )
        review = Candidate(
            id="fake:review.txt",
            category="fake.file",
            size_bytes=5,
            path_or_handle=str(home / "review.txt"),
            confidence=Confidence.REVIEW,
            reason="review",
        )
        report = _make_report(safe, review)

        plan_safe = _audit_to_plan(report, safe_only=True)
        assert len(plan_safe.candidates) == 1
        assert plan_safe.candidates[0].confidence == Confidence.SAFE

        plan_all = _audit_to_plan(report, safe_only=False, include_review=True)
        assert len(plan_all.candidates) == 2

        plan_default = _audit_to_plan(report)
        assert len(plan_default.candidates) == 1  # Only SAFE by default

    def test_signed_plan_roundtrip(self, tmp_path):
        """Create a signed plan, save it, load it, verify signature."""
        from bekas.signing import sign_plan, verify_plan

        home = tmp_path / "home"
        home.mkdir()
        f1 = home / "file.txt"
        f1.write_text("x")

        plugin = FakeFilePlugin(home)
        cands = list(plugin.discover(Context(dry_run=True, config={})))
        report = _make_report(*cands)
        plan = _audit_to_plan(report)

        plan_data = plan.model_dump(mode="json")
        signature = sign_plan(plan_data)
        plan_data["_signature"] = signature

        # Save and load
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan_data))
        loaded = json.loads(plan_file.read_text())
        loaded_sig = loaded.pop("_signature")
        assert verify_plan(loaded, loaded_sig) is True

        # Tampered plan fails verification
        loaded["audit_id"] = "tampered"
        assert verify_plan(loaded, loaded_sig) is False

    def test_runner_with_fake_plugins(self, tmp_path):
        """run_audit orchestrates multiple fake plugins correctly."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "a.txt").write_text("aa")
        (home / "b.txt").write_text("bbb")

        p1 = FakeFilePlugin(home)
        report = run_audit([p1], serial=True)
        assert report.summary.total_candidates == 2
        assert report.summary.total_bytes == 5
        assert report.audit_id is not None

    def test_apply_plan_excluded_paths(self, tmp_path):
        """Excluded paths are silently skipped during apply."""
        home = tmp_path / "home"
        home.mkdir()
        f1 = home / "file.txt"
        f1.write_text("x")

        # Use an excluded path
        excluded = Candidate(
            id="fake:etc",
            category="fake.file",
            size_bytes=1,
            path_or_handle="/etc/passwd",
            confidence=Confidence.SAFE,
            reason="excluded",
        )
        plan = Plan(audit_id="a1", candidates=[excluded])
        ctx = Context(dry_run=False, config={})
        result = apply_plan(plan, [], ctx, yes_all=True)
        assert result.total_bytes_freed == 0
        assert len(result.per_candidate) == 0  # Excluded before processing
