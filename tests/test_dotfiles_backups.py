"""Tests for dotfiles_backups plugin."""

from pathlib import Path

from bekas.models import Context, RemovalResult
from bekas.plugins.dotfiles_backups import DotfilesBackupsPlugin


def test_discover_and_remove(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    bak = home / ".zshrc.bak"
    bak.write_text("backup")
    old = home / ".bashrc.old"
    old.write_text("old")
    normal = home / ".zshrc"
    normal.write_text("current")

    p = DotfilesBackupsPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    ids = [c.id for c in candidates]
    assert any("zshrc.bak" in i for i in ids)
    assert any("bashrc.old" in i for i in ids)
    assert not any(i.split(":")[1] == "zshrc" for i in ids if ".zshrc.bak" not in i and ".zshrc.old" not in i)

    # Remove
    c = next(c for c in candidates if "zshrc.bak" in c.id)
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert not bak.exists()


def test_supports():
    p = DotfilesBackupsPlugin()
    assert p.supports_quarantine() is True
    assert p.supports_undo() is False
