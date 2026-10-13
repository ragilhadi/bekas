"""Tests for go_modcache plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.go_modcache import GoModcachePlugin


def test_empty_when_no_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = GoModcachePlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_yields_candidate_with_fake_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / "go" / "pkg" / "mod"
    cache.mkdir(parents=True)
    (cache / "mod").write_text("data")

    p = GoModcachePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    with patch("shutil.which", return_value="/usr/bin/go"):
        candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "go.modcache"
    assert c.confidence == Confidence.SAFE
    assert c.size_bytes > 0


def test_gopath_override(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    gopath = tmp_path / "custom_go"
    cache = gopath / "pkg" / "mod"
    cache.mkdir(parents=True)
    (cache / "mod").write_text("data")
    monkeypatch.setenv("GOPATH", str(gopath))
    monkeypatch.delenv("GOMODCACHE", raising=False)

    p = GoModcachePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    assert str(cache) in candidates[0].metadata.get("paths", [])


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_discover_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GOPATH", raising=False)
    monkeypatch.delenv("GOMODCACHE", raising=False)
    cache = tmp_path / "go" / "pkg" / "mod"
    cache.mkdir(parents=True)
    (cache / "example.com" / "mod.zip").parent.mkdir(parents=True)
    (cache / "example.com" / "mod.zip").write_text("x" * 64)

    p = GoModcachePlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1

    result = p.remove(candidates[0], ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 64
    assert not cache.exists()
    assert "Deleted" in result.log


def test_remove_skips_missing_paths(tmp_path):
    p = GoModcachePlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="go:modcache",
        category="go.modcache",
        size_bytes=0,
        path_or_handle=str(tmp_path / "gone"),
        confidence=Confidence.SAFE,
        reason="t",
        metadata={"paths": [str(tmp_path / "gone")]},
    )
    result = p.remove(c, ctx)
    assert result.success is True
    assert result.bytes_freed == 0


def test_remove_failure_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GOPATH", raising=False)
    monkeypatch.delenv("GOMODCACHE", raising=False)
    cache = tmp_path / "go" / "pkg" / "mod"
    cache.mkdir(parents=True)
    (cache / "f").write_text("data")

    p = GoModcachePlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(candidates[0], ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Failed to delete" in result.log
    assert cache.exists()
