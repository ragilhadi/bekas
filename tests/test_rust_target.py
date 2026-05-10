"""Tests for rust_target plugin."""

from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.rust_target import RustTargetPlugin, _du, _find_targets


def test_find_targets(tmp_path):
    target = tmp_path / "project" / "target"
    target.mkdir(parents=True)
    (target / "debug").mkdir()
    (target / "debug" / "app").write_text("bin")
    # Need a marker: Cargo.toml in parent, or .rustc_info.json inside target
    (tmp_path / "project" / "Cargo.toml").write_text("[package]\n")
    seen: set[Path] = set()
    found = list(_find_targets(tmp_path, seen))
    assert any(target.resolve() == f for f in found)


def test_discover_old_target(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    code = home / "code"
    code.mkdir()

    # Stale target with old mtime and marker
    stale = code / "stale" / "target"
    stale.mkdir(parents=True)
    (code / "stale" / "Cargo.toml").write_text("[package]\n")
    old_mtime = __import__("time").time() - (200 * 24 * 3600)
    __import__("os").utime(stale, (old_mtime, old_mtime))

    # Recent target with marker (should not yield)
    recent = code / "recent" / "target"
    recent.mkdir(parents=True)
    (code / "recent" / "Cargo.toml").write_text("[package]\n")

    p = RustTargetPlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"rust.target": {"min_idle_days": 90}}})
    candidates = list(p.discover(ctx))
    ids = [c.id for c in candidates]
    assert any("stale" in i for i in ids)
    assert not any("recent" in i for i in ids)

    stale_c = next(c for c in candidates if "stale" in c.id)
    assert stale_c.confidence == Confidence.SAFE


def test_remove():
    p = RustTargetPlugin()
    c = Candidate(
        id="rust:/tmp/fake/target",
        category="rust.target",
        size_bytes=0,
        path_or_handle="/tmp/fake/target",
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=True, config={})
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)


def test_du(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    assert _du(d) == 5
