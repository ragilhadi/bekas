"""Regression tests for protected locations, symlinks, recency and exclusion globs."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bekas.clean import apply_plan
from bekas.models import Candidate, Confidence, Context, Plan, RemovalResult
from bekas.plugin import Plugin
from bekas.safety import filter_candidates, is_excluded, is_safe_to_delete

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX system paths")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "me"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@posix_only
@pytest.mark.parametrize(
    "path", ["/", "/usr", "/usr/lib/x", "/lib", "/lib64", "/opt", "/root", "/home", "/var", "/etc", "/mnt"]
)
def test_system_roots_and_libs_are_excluded(path):
    assert is_excluded(path) is True


def test_home_itself_is_excluded(fake_home):
    assert is_excluded(fake_home) is True
    assert is_excluded(fake_home.parent) is True  # contains $HOME


def test_home_children_are_still_candidates(fake_home):
    target = fake_home / ".cache" / "pip"
    target.mkdir(parents=True)
    assert is_excluded(target) is False


def test_ancestor_of_sensitive_dir_is_excluded(fake_home):
    (fake_home / ".ssh").mkdir()
    safe, reason = is_safe_to_delete(fake_home)
    assert safe is False
    assert "protected" in reason or "contains" in reason


def test_ancestor_of_bekas_data_dir_is_excluded():
    from bekas.config import _data_dir

    assert is_excluded(_data_dir().parent) is True


@posix_only
def test_symlink_pointing_outside_home_is_excluded(fake_home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = fake_home / "link"
    link.symlink_to(outside)
    safe, reason = is_safe_to_delete(link)
    assert safe is False
    assert "symlink" in reason


@posix_only
def test_symlink_inside_home_is_allowed(fake_home):
    real = fake_home / "real"
    real.mkdir()
    link = fake_home / "link"
    link.symlink_to(real)
    assert is_excluded(link) is False


def test_recent_files_excluded_when_quiet_hours_set(fake_home):
    f = fake_home / "fresh.txt"
    f.write_text("x")
    assert is_excluded(f, min_quiet_hours=6) is True
    old = time.time() - 7 * 3600
    os.utime(f, (old, old))
    assert is_excluded(f, min_quiet_hours=6) is False


def _cand(path: Path) -> Candidate:
    return Candidate(
        id=f"t:{path.name}",
        category="fake.test",
        size_bytes=1,
        path_or_handle=str(path),
        confidence=Confidence.SAFE,
        reason="r",
    )


def test_filter_candidates_applies_quiet_hours(fake_home):
    f = fake_home / "fresh.txt"
    f.write_text("x")
    assert filter_candidates([_cand(f)], None, 6) == []
    assert len(filter_candidates([_cand(f)], None, 0)) == 1


def test_run_audit_uses_profile_min_quiet_hours(fake_home, monkeypatch):
    from bekas.config import Profile
    from bekas.runner import run_audit

    f = fake_home / "fresh.txt"
    f.write_text("x")

    class P(Plugin):
        name = "fake.test"
        description = "d"

        def discover(self, ctx):
            yield _cand(f)

    profile = Profile(enabled_plugins=["*"], min_quiet_hours=6)
    monkeypatch.setattr("bekas.runner.profile_for", lambda n=None: profile)
    report = run_audit([P()], serial=True, no_cache=True)
    assert report.summary.total_candidates == 0


class _Remover(Plugin):
    name = "fake.test"
    description = "d"

    def __init__(self) -> None:
        self.removed: list[str] = []

    def discover(self, ctx):
        yield from []

    def remove(self, candidate, ctx):
        self.removed.append(candidate.id)
        return RemovalResult(success=True, bytes_freed=1, log="deleted")


def test_apply_plan_honours_exclusions_added_after_plan(fake_home):
    f = fake_home / "keep" / "data.bin"
    f.parent.mkdir()
    f.write_text("x")
    plugin = _Remover()
    plan = Plan(audit_id="a", candidates=[_cand(f)])
    cfg = type("C", (), {"exclude": [str(f.parent)]})()
    with patch("bekas.clean.load_config", return_value=cfg):
        result = apply_plan(plan, [plugin], Context(dry_run=False), yes_all=True)
    assert plugin.removed == []
    assert "user exclusion" in result.per_candidate[0][1].log


def test_apply_plan_skips_recently_modified(fake_home):
    f = fake_home / "fresh.bin"
    f.write_text("x")
    plugin = _Remover()
    plan = Plan(audit_id="a", candidates=[_cand(f)])
    profile = type("Pr", (), {"min_quiet_hours": 6, "quarantine_enabled": False, "quarantine_retention_days": 30})()
    profile.model_dump = lambda: {"quarantine_retention_days": 30}
    with patch("bekas.clean.profile_for", return_value=profile):
        apply_plan(plan, [plugin], Context(dry_run=False), yes_all=True)
    assert plugin.removed == []


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("node_modules", "/home/me/proj/node_modules/x/y.js", True),
        ("node_modules", "/home/me/proj/src/main.py", False),
        ("*.bak", "/home/me/a/b.bak", True),
        ("*.bak", "/home/me/a/b.bak.txt", False),
        ("**/foo/*.log", "/home/me/x/foo/a.log", True),
        ("**/foo/*.log", "/home/me/x/foo/sub/a.log", False),
        ("**/foo/*.log", "/home/me/x/bar/a.log", False),
        ("**/foo/**", "/home/me/x/foo/deep/er/file", True),
        ("/data/keep", "/data/keep/a/b", True),
        ("/data/keep", "/other/data/keep", False),
        ("/data/ke?p", "/data/keep/a", True),
        ("/data/[ab]x", "/data/ax/f", True),
        ("/data/[!ab]x", "/data/ax/f", False),
    ],
)
def test_user_exclusion_globs(pattern, path, expected):
    from bekas.safety import _user_exclusion_match

    assert (_user_exclusion_match(path, Path(path), [pattern]) is not None) is expected


def test_tilde_only_expanded_at_start(fake_home):
    from bekas.safety import _normalise_user_pattern

    assert _normalise_user_pattern("~/work") == f"{fake_home.as_posix()}/work"
    assert _normalise_user_pattern("/data/~/x") == "/data/~/x"
