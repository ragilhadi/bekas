"""Tests for browser_chrome plugin."""

from pathlib import Path

from bekas.models import Confidence, Context
from bekas.plugins.browser_chrome import (
    BrowserChromePlugin,
    _chrome_cache_dirs,
    _chrome_profiles,
)


def test_no_profiles_when_home_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _chrome_profiles() == []
    assert _chrome_cache_dirs() == []


def test_scans_fake_profile_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".config" / "google-chrome" / "Default" / "Cache"
    cache.mkdir(parents=True)
    (cache / "data").write_text("x")

    p = BrowserChromePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    candidates = list(p.discover(ctx))
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.category == "browser.chrome"
    assert c.confidence == Confidence.REVIEW
    assert c.size_bytes > 0
