"""Tests for downloads plugin."""

import time
from pathlib import Path

from bekas.models import Context, RemovalResult
from bekas.plugins.downloads import DownloadsPlugin


def test_discover_and_remove(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)

    old_dl = downloads / "old_file.zip"
    old_dl.write_text("data")
    old_atime = time.time() - (200 * 24 * 3600)
    import os

    os.utime(old_dl, (old_atime, old_atime))

    new_dl = downloads / "new_file.zip"
    new_dl.write_text("data")

    p = DownloadsPlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"downloads": {"min_age_days": 180}}})
    candidates = list(p.discover(ctx))
    assert any(c.id == f"dl:{old_dl.name}" for c in candidates)
    assert not any(c.id == f"dl:{new_dl.name}" for c in candidates)

    # Remove
    c = next(c for c in candidates if c.id == f"dl:{old_dl.name}")
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True


def test_quarantine_support():
    p = DownloadsPlugin()
    assert p.supports_quarantine() is True
