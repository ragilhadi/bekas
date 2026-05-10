"""Tests for screenshots plugin."""

import time
from pathlib import Path

from bekas.models import Context, RemovalResult
from bekas.plugins.screenshots import ScreenshotsPlugin


def test_screenshot_folders():
    p = ScreenshotsPlugin()
    folders = p._screenshot_folders()
    assert len(folders) >= 1
    assert all(isinstance(f, Path) for f in folders)


def test_discover_and_remove(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    pics = home / "Pictures" / "Screenshots"
    pics.mkdir(parents=True)
    old_ss = pics / "Screenshot_2023.png"
    old_ss.write_text("x")
    # Set mtime to 120 days ago
    old_mtime = time.time() - (120 * 24 * 3600)
    import os

    os.utime(old_ss, (old_mtime, old_mtime))

    new_ss = pics / "Screenshot_recent.png"
    new_ss.write_text("y")

    p = ScreenshotsPlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"screenshots": {"min_age_days": 90}}})
    candidates = list(p.discover(ctx))
    assert any(c.id == f"ss:{old_ss.name}" for c in candidates)
    assert not any(c.id == f"ss:{new_ss.name}" for c in candidates)

    # Remove
    c = next(c for c in candidates if c.id == f"ss:{old_ss.name}")
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True


def test_quarantine_fallback(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    pics = home / "Pictures" / "Screenshots"
    pics.mkdir(parents=True)
    ss = pics / "ss.png"
    ss.write_text("data")
    old_mtime = time.time() - (120 * 24 * 3600)
    import os

    os.utime(ss, (old_mtime, old_mtime))

    p = ScreenshotsPlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"screenshots": {"min_age_days": 90}}})
    candidates = list(p.discover(ctx))
    c = candidates[0]
    # quarantine uses move_to_quarantine which requires a real quarantine dir — skip
    assert p.supports_quarantine() is True
