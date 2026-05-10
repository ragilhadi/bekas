"""Tests for system_tmp plugin."""

import os
import time
from pathlib import Path

from bekas.models import Context, RemovalResult
from bekas.plugins.system_tmp import SystemTmpPlugin


def test_discover_and_remove(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    # Point tempfile to tmp_path so /tmp fallback isn't needed
    import tempfile as tf_mod

    monkeypatch.setattr(tf_mod, "gettempdir", lambda: str(tmp_path))

    # Create an old temp file by current user
    old_file = tmp_path / "old_temp.txt"
    old_file.write_text("temp")
    old_mtime = time.time() - (60 * 24 * 3600)
    os.utime(old_file, (old_mtime, old_mtime))

    # Create a recent temp file
    new_file = tmp_path / "new_temp.txt"
    new_file.write_text("temp")

    p = SystemTmpPlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"system.tmp": {"min_age_days": 30}}})
    candidates = list(p.discover(ctx))
    assert any(c.path_or_handle == str(old_file) for c in candidates)
    assert not any(c.path_or_handle == str(new_file) for c in candidates)

    # Remove
    c = next(c for c in candidates if c.path_or_handle == str(old_file))
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
