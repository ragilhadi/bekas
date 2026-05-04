"""Tests for xcode_derived_data plugin."""

import sys
from pathlib import Path

from bekas.models import Confidence, Context, RemovalResult
from bekas.plugins.xcode_derived_data import XcodeDerivedDataPlugin


def test_platform_gated_on_linux():
    """Plugin should not be available on non-darwin platforms."""
    p = XcodeDerivedDataPlugin()
    ctx = Context(dry_run=True, config={})
    if sys.platform != "darwin":
        assert p.is_available(ctx) is False
    else:
        # On macOS, it depends on dir existence
        assert isinstance(p.is_available(ctx), bool)


def test_discover_and_remove(tmp_path, monkeypatch):
    """Create fake DerivedData entries and verify discover + remove."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    derived = home / "Library" / "Developer" / "Xcode" / "DerivedData"
    derived.mkdir(parents=True)

    # Create an old project entry with info.plist 60 days ago
    old_project = derived / "project1-abc"
    old_project.mkdir()
    plist = old_project / "info.plist"
    import time
    old_mtime = time.time() - (70 * 24 * 3600)
    plist.write_text("dummy")
    import os
    os.utime(plist, (old_mtime, old_mtime))

    # Create a recent entry
    new_project = derived / "project2-def"
    new_project.mkdir()
    new_plist = new_project / "info.plist"
    new_plist.write_text("dummy")

    p = XcodeDerivedDataPlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"xcode.derived_data": {"min_idle_days": 30}}})
    candidates = list(p.discover(ctx))
    ids = [c.id for c in candidates]
    assert any("project1" in i for i in ids)
    assert not any("project2" in i for i in ids)
    assert all(c.confidence == Confidence.SAFE for c in candidates)

    # Remove
    c = candidates[0]
    ctx_remove = Context(dry_run=False, config={})
    result = p.remove(c, ctx_remove)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert not old_project.exists()


def test_supports_quarantine_is_false():
    p = XcodeDerivedDataPlugin()
    assert p.supports_quarantine() is False
    assert p.supports_undo() is False
