"""Tests for npm_cacache plugin."""

from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.npm_cacache import NpmCacachePlugin, _du


def test_not_available_when_empty(monkeypatch, tmp_path):
    """When ~/.npm does not exist, is_available returns False and discovery is empty."""
    p = NpmCacachePlugin()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_discover_both_subdirs(tmp_path, monkeypatch):
    """Create _cacache and _logs and discover both as SAFE candidates."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cacache = home / ".npm" / "_cacache"
    logs = home / ".npm" / "_logs"
    (cacache / "content-v2" / "sha512").mkdir(parents=True)
    (cacache / "content-v2" / "sha512" / "blob").write_text("package tarball")
    (cacache / "index-v5").mkdir()
    (cacache / "index-v5" / "entry").write_text("idx")
    logs.mkdir()
    (logs / "2026-11-22T10_00_00-debug-0.log").write_text("log line")

    p = NpmCacachePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    candidates = list(p.discover(ctx))

    assert sorted(c.category for c in candidates) == ["npm.cacache", "npm.cacache"]
    assert sorted(c.id for c in candidates) == ["npm:cacache", "npm:logs"]
    for c in candidates:
        assert c.confidence == Confidence.SAFE
    sizes = {c.id: c.size_bytes for c in candidates}
    assert sizes["npm:cacache"] == len("package tarball") + len("idx")
    assert sizes["npm:logs"] == len("log line")


def test_discover_skips_empty_subdir(tmp_path, monkeypatch):
    """An empty _logs directory yields no candidate."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    (home / ".npm" / "_logs").mkdir(parents=True)

    p = NpmCacachePlugin()
    ctx = Context(dry_run=True, config={})
    assert list(p.discover(ctx)) == []


def test_remove_dry_run_leaves_files(tmp_path, monkeypatch):
    """Dry-run removal must not touch _cacache."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cacache = home / ".npm" / "_cacache"
    (cacache / "content-v2").mkdir(parents=True)
    (cacache / "content-v2" / "blob").write_text("data")

    p = NpmCacachePlugin()
    ctx = Context(dry_run=True, config={})
    c = next(iter(p.discover(ctx)))
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.log == "dry-run"
    assert cacache.exists()
    assert (cacache / "content-v2" / "blob").exists()


def test_remove_success(tmp_path, monkeypatch):
    """Real removal deletes only the targeted _cacache dir, leaves ~/.npm."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cacache = home / ".npm" / "_cacache"
    (cacache / "content-v2").mkdir(parents=True)
    (cacache / "content-v2" / "blob").write_text("x" * 2048)
    logs = home / ".npm" / "_logs"
    logs.mkdir()
    (logs / "debug.log").write_text("keep me")

    p = NpmCacachePlugin()
    ctx = Context(dry_run=False, config={})
    candidates = {c.id: c for c in p.discover(ctx)}
    result = p.remove(candidates["npm:cacache"], ctx)
    assert result.success is True
    assert result.bytes_freed >= 2048
    assert not cacache.exists()
    assert logs.exists()
    assert (logs / "debug.log").exists()


def test_remove_refuses_unexpected_path(tmp_path, monkeypatch):
    """Removal refuses a path outside the expected ~/.npm/_* layout."""
    p = NpmCacachePlugin()
    ctx = Context(dry_run=False, config={})
    bad = Candidate(
        id="npm:bad",
        category="npm.cacache",
        size_bytes=10,
        path_or_handle=str(tmp_path / ".npm"),
        confidence=Confidence.SAFE,
        reason="test",
    )
    result = p.remove(bad, ctx)
    assert result.success is False
    assert "Refusing" in result.log


def test_du_on_directory(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    (d / "f2").write_text("world")
    assert _du(d) == 10
