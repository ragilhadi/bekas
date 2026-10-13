"""Tests for browser_firefox plugin."""

import os
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.browser_firefox import (
    BrowserFirefoxPlugin,
    _firefox_cache_dirs,
    _firefox_profiles,
)


def _fake_profile_dir(home: Path) -> Path:
    """Return the platform-appropriate Firefox profile dir under a fake home."""
    if os.name == "nt":
        return home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles" / "xxxx.default"
    return home / ".mozilla" / "firefox" / "xxxx.default"


def test_no_profiles_when_home_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _firefox_profiles() == []
    assert _firefox_cache_dirs() == []


def test_scans_fake_profile_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _fake_profile_dir(tmp_path) / "cache2"
    cache.mkdir(parents=True)
    (cache / "entry").write_text("x")

    p = BrowserFirefoxPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    candidates = list(p.discover(ctx))
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.category == "browser.firefox"
    assert c.confidence == Confidence.REVIEW
    assert c.size_bytes > 0


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_discover_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _fake_profile_dir(tmp_path) / "cache2"
    cache.mkdir(parents=True)
    (cache / "entry").write_text("x" * 64)

    p = BrowserFirefoxPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1

    result = p.remove(candidates[0], ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 64
    assert not cache.exists()
    assert result.log == "Deleted"


def test_remove_missing_path():
    p = BrowserFirefoxPlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="firefox:cache2",
        category="browser.firefox",
        size_bytes=0,
        path_or_handle="/nonexistent/bekas/cache2",
        confidence=Confidence.REVIEW,
        reason="t",
        metadata={"path": "/nonexistent/bekas/cache2"},
    )
    result = p.remove(c, ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Path does not exist" in result.log


def test_remove_failure_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _fake_profile_dir(tmp_path) / "cache2"
    cache.mkdir(parents=True)
    (cache / "entry").write_text("x")

    p = BrowserFirefoxPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(candidates[0], ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert cache.exists()


def test_windows_profile_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base = tmp_path / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles" / "xxxx.default"
    cache = base / "cache2"
    cache.mkdir(parents=True)
    (cache / "entry").write_text("x")

    profiles = _firefox_profiles()
    assert len(profiles) == 1
    dirs = _firefox_cache_dirs()
    assert len(dirs) == 1
