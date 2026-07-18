"""Tests for browser_chrome plugin."""

import os
from pathlib import Path

from bekas.models import Confidence, Context
from bekas.plugins.browser_chrome import (
    BrowserChromePlugin,
    _chrome_cache_dirs,
    _chrome_profiles,
)


def _fake_profile_dir(home: Path) -> Path:
    """Return the platform-appropriate Chrome profile dir under a fake home."""
    if os.name == "nt":
        return home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default"
    return home / ".config" / "google-chrome" / "Default"


def test_no_profiles_when_home_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _chrome_profiles() == []
    assert _chrome_cache_dirs() == []


def test_scans_fake_profile_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = _fake_profile_dir(tmp_path) / "Cache"
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
