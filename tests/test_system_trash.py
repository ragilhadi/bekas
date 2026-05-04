"""Tests for system_trash plugin."""

import sys
from pathlib import Path

import pytest

from bekas.models import Confidence, Context, RemovalResult
from bekas.plugins.system_trash import SystemTrashPlugin, _delete_with_trashinfo, _du, _file_size, _trash_paths


def test_trash_paths():
    paths = _trash_paths()
    assert len(paths) >= 1
    home = Path.home()
    if sys.platform == "darwin":
        assert str(paths[0]) == str(home / ".Trash")
    else:
        assert str(paths[0]) == str(home / ".local" / "share" / "Trash" / "files")


def test_discover_and_remove(tmp_path, monkeypatch):
    """Create fake trash items and verify discover + remove."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    trash = home / ".Trash" if sys.platform == "darwin" else home / ".local" / "share" / "Trash" / "files"
    trash.mkdir(parents=True)

    item1 = trash / "old_doc.txt"
    item1.write_text("trash data")
    dir1 = trash / "old_folder"
    dir1.mkdir()
    (dir1 / "nested").write_text("nested")

    p = SystemTrashPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 2
    assert all(c.confidence == Confidence.SAFE for c in candidates)

    # Remove a file
    c_file = next(c for c in candidates if not c.metadata.get("is_dir"))
    ctx_remove = Context(dry_run=False, config={})
    result = p.remove(c_file, ctx_remove)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert not item1.exists()

    # Remove a directory
    c_dir = next(c for c in candidates if c.metadata.get("is_dir"))
    result = p.remove(c_dir, ctx_remove)
    assert result.success is True
    assert not dir1.exists()


def test_delete_with_trashinfo_linux(tmp_path, monkeypatch):
    """On Linux, deleting a trash item should also delete the .trashinfo file."""
    if sys.platform == "darwin":
        pytest.skip("Linux-specific test")

    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    files_dir = home / ".local" / "share" / "Trash" / "files"
    info_dir = home / ".local" / "share" / "Trash" / "info"
    files_dir.mkdir(parents=True)
    info_dir.mkdir(parents=True)

    item = files_dir / "foo.txt"
    item.write_text("hello")
    trashinfo = info_dir / "foo.txt.trashinfo"
    trashinfo.write_text("[Trash Info]")

    _delete_with_trashinfo(item)
    assert not item.exists()
    assert not trashinfo.exists()


def test_file_size(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    assert _file_size(f) == 5
    assert _file_size(tmp_path / "nonexistent") == 0


def test_du(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    (d / "f2").write_text("world")
    assert _du(d) == 10
