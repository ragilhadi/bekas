"""Tests for browser_firefox plugin."""

from pathlib import Path

from bekas.models import Confidence, Context
from bekas.plugins.browser_firefox import (
    BrowserFirefoxPlugin,
    _firefox_cache_dirs,
    _firefox_profiles,
)


def test_no_profiles_when_home_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _firefox_profiles() == []
    assert _firefox_cache_dirs() == []


def test_scans_fake_profile_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".mozilla" / "firefox" / "xxxx.default" / "cache2"
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
