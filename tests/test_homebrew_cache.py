"""Tests for homebrew_cache plugin."""

from pathlib import Path

from bekas.models import Confidence, Context, RemovalResult
from bekas.plugins.homebrew_cache import HomebrewCachePlugin, _du


def test_not_available_when_empty(monkeypatch, tmp_path):
    """When no Homebrew cache dirs exist, is_available returns False."""
    p = HomebrewCachePlugin()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_discover_macos_location(tmp_path, monkeypatch):
    """Create a macOS-style cache dir and discover it."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cache = home / "Library" / "Caches" / "Homebrew"
    cache.mkdir(parents=True)
    (cache / "downloads").mkdir()
    (cache / "downloads" / "wget--2.0.cask.bottle.tar.gz").write_text("bottle data")
    (cache / "api").mkdir()
    (cache / "api" / "formula.jws.json").write_text("meta")

    p = HomebrewCachePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    c = next(iter(p.discover(ctx)))

    assert c.category == "homebrew.cache"
    assert c.confidence == Confidence.SAFE
    assert c.id == "homebrew:cache"
    assert c.size_bytes == len("bottle data") + len("meta")
    assert str(cache) in c.metadata["paths"]


def test_discover_linuxbrew_location(tmp_path, monkeypatch):
    """Create a Linuxbrew-style cache dir and discover it."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cache = home / ".cache" / "Homebrew"
    cache.mkdir(parents=True)
    (cache / "api").mkdir()
    (cache / "api" / "formula.jws.json").write_text("meta")

    p = HomebrewCachePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    c = next(iter(p.discover(ctx)))
    assert str(cache) in c.metadata["paths"]


def test_remove_dry_run_leaves_files(tmp_path, monkeypatch):
    """Dry-run removal must not touch the cache."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cache = home / "Library" / "Caches" / "Homebrew"
    cache.mkdir(parents=True)
    (cache / "payload.bin").write_text("data")

    p = HomebrewCachePlugin()
    ctx = Context(dry_run=True, config={})
    c = next(iter(p.discover(ctx)))
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.log == "dry-run"
    assert cache.exists()
    assert (cache / "payload.bin").exists()


def test_remove_success(tmp_path, monkeypatch):
    """Real removal deletes the cache directories."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cache = home / "Library" / "Caches" / "Homebrew"
    cache.mkdir(parents=True)
    (cache / "payload.bin").write_text("x" * 4096)

    p = HomebrewCachePlugin()
    ctx = Context(dry_run=False, config={})
    c = next(iter(p.discover(ctx)))
    assert c.size_bytes == 4096
    result = p.remove(c, ctx)
    assert result.success is True
    assert result.bytes_freed >= 4096
    assert not cache.exists()


def test_du_on_file_and_dir(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert _du(f) == 5
    d = tmp_path / "d"
    d.mkdir()
    (d / "f1").write_text("hi")
    (d / "f2").write_text("world")
    assert _du(d) == 7
