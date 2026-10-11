"""Regression tests: signed plans must be authentic, fingerprinted and fresh."""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from bekas.clean import fingerprint_candidate, fingerprint_plan, validate_plan_freshness
from bekas.cli import cli
from bekas.models import Candidate, Confidence, RemovalResult
from bekas.plugin import Plugin
from bekas.signing import _get_or_create_key, sign_plan, verify_plan


def _cand(path: Path | str, cid: str = "f:1", category: str = "fake.test", size: int = 5) -> Candidate:
    return Candidate(
        id=cid,
        category=category,
        size_bytes=size,
        path_or_handle=str(path),
        confidence=Confidence.SAFE,
        reason="r",
    )


class _FilePlugin(Plugin):
    name = "fake.test"
    description = "d"
    target: Path

    def discover(self, ctx):
        yield _cand(self.target, size=self.target.stat().st_size)

    def remove(self, candidate, ctx):
        Path(candidate.path_or_handle).unlink()
        return RemovalResult(success=True, bytes_freed=candidate.size_bytes, log="deleted")


@pytest.fixture
def target(tmp_path, monkeypatch):
    f = tmp_path / "victim.bin"
    f.write_text("hello")
    plugin = _FilePlugin()
    plugin.target = f
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [plugin])
    return f


@pytest.fixture
def runner():
    return CliRunner()


def _save(runner, tmp_path) -> tuple[Path, dict]:
    plan_file = tmp_path / "plan.json"
    result = runner.invoke(cli, ["plan", "--save", str(plan_file)])
    assert result.exit_code == 0, result.output
    return plan_file, json.loads(plan_file.read_text())


# ── unsigned plans ─────────────────────────────────────────────


def test_unsigned_plan_is_rejected(runner, target, tmp_path):
    plan_file, data = _save(runner, tmp_path)
    data.pop("_signature")
    plan_file.write_text(json.dumps(data))
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--plan-file", str(plan_file)])
    assert result.exit_code == 1
    assert "no signature" in result.output
    assert target.exists()


def test_unsigned_plan_accepted_with_flag(runner, target, tmp_path):
    plan_file, data = _save(runner, tmp_path)
    data.pop("_signature")
    plan_file.write_text(json.dumps(data))
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--allow-unsigned", "--plan-file", str(plan_file)])
    assert result.exit_code == 0, result.output
    assert not target.exists()


def test_allow_unsigned_does_not_skip_verification_of_signed_plan(runner, target, tmp_path):
    plan_file, data = _save(runner, tmp_path)
    data["audit_id"] = "tampered"
    plan_file.write_text(json.dumps(data))
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--allow-unsigned", "--plan-file", str(plan_file)])
    assert result.exit_code == 1
    assert "signature is invalid" in result.output
    assert target.exists()


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"_signature": 5}'])
def test_garbage_plan_files_fail_cleanly(runner, tmp_path, content):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(content)
    result = runner.invoke(cli, ["clean", "--apply", "--plan-file", str(plan_file)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_tampered_fingerprints_are_rejected(runner, target, tmp_path):
    plan_file, data = _save(runner, tmp_path)
    fid = next(iter(data["fingerprints"]))
    data["fingerprints"][fid]["size_bytes"] = 10**9
    plan_file.write_text(json.dumps(data))
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--plan-file", str(plan_file)])
    assert result.exit_code == 1


# ── saved plans carry fingerprints and a timestamp ─────────────


def test_saved_plan_has_fingerprints_and_signed_at(runner, target, tmp_path):
    _, data = _save(runner, tmp_path)
    assert data["signed_at"] is not None
    fp = data["fingerprints"]["f:1"]
    assert fp["exists"] is True
    assert fp["size_bytes"] == 5
    assert isinstance(fp["mtime"], int)


def test_modified_candidate_is_skipped_on_apply(runner, target, tmp_path):
    plan_file, _ = _save(runner, tmp_path)
    target.write_text("hello, but much longer now")
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--plan-file", str(plan_file)])
    assert target.exists()
    assert "stale" in result.output


def test_force_stale_overrides(runner, target, tmp_path):
    plan_file, _ = _save(runner, tmp_path)
    target.write_text("hello, but much longer now")
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--force-stale", "--plan-file", str(plan_file)])
    assert result.exit_code == 0, result.output
    assert not target.exists()


def test_unchanged_candidate_is_applied(runner, target, tmp_path):
    plan_file, _ = _save(runner, tmp_path)
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--plan-file", str(plan_file)])
    assert result.exit_code == 0, result.output
    assert not target.exists()


def test_expired_plan_warns_visibly(runner, target, tmp_path):
    plan_file, data = _save(runner, tmp_path)
    # Re-sign an old copy so only the age differs
    data.pop("_signature")
    data["signed_at"] = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    data["_signature"] = sign_plan(data)
    plan_file.write_text(json.dumps(data))
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--plan-file", str(plan_file)])
    assert ">7 days old" in result.output


# ── fingerprints of directories and opaque handles ─────────────


def test_directory_growth_is_detected(tmp_path):
    d = tmp_path / "cache"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "a.bin").write_bytes(b"x" * 10)
    c = _cand(d, size=10)
    from bekas.models import Plan

    plan = Plan(audit_id="a", candidates=[c], fingerprints=fingerprint_plan(Plan(audit_id="a", candidates=[c])))
    assert plan.fingerprints[c.id]["size_bytes"] == 10

    (d / "sub" / "b.bin").write_bytes(b"y" * 100)  # nested growth
    valid, skipped = validate_plan_freshness(plan)
    assert valid == []
    assert any("stale" in s for s in skipped)


def test_unchanged_directory_stays_valid(tmp_path):
    from bekas.models import Plan

    d = tmp_path / "cache"
    d.mkdir()
    (d / "a.bin").write_bytes(b"x" * 10)
    c = _cand(d)
    plan = Plan(audit_id="a", candidates=[c], fingerprints=fingerprint_plan(Plan(audit_id="a", candidates=[c])))
    valid, skipped = validate_plan_freshness(plan)
    assert valid == [c]
    assert skipped == []


def test_opaque_handles_survive_validation():
    from bekas.models import Plan

    c = _cand("docker://abc", category="docker.image.dangling")
    plan = Plan(audit_id="a", candidates=[c], fingerprints={"other": {"size_bytes": 1, "mtime": 1}})
    valid, _ = validate_plan_freshness(plan)
    assert valid == [c]
    assert fingerprint_candidate(c) == {}


def test_git_branch_candidates_are_not_fingerprinted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert fingerprint_candidate(_cand(repo, category="git.branch")) == {}


# ── signing key ────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_signing_key_is_created_owner_only(monkeypatch, tmp_path):
    seen: list[int] = []
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **k):
        if str(path).endswith("plan_signing.key"):
            seen.append(mode)
        return real_open(path, flags, mode, *a, **k)

    monkeypatch.setattr("bekas.signing.os.open", spy)
    key = _get_or_create_key()
    from bekas.config import data_dir

    mode = stat.S_IMODE((data_dir() / "plan_signing.key").stat().st_mode)
    assert seen == [0o600]
    assert mode == 0o600
    assert len(key) == 32
    assert _get_or_create_key() == key


def test_existing_key_is_reused_and_verifies():
    data = {"audit_id": "x"}
    assert verify_plan(data, sign_plan(data))
