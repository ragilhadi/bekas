"""Tests for go_modcache plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Confidence, Context
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
