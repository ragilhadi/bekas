"""Branch coverage tests for plugin edge cases: downloads, screenshots, dotfiles, rust_target."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bekas.models import Candidate, Confidence, Context
from bekas.plugins.dotfiles_backups import DotfilesBackupsPlugin
from bekas.plugins.downloads import DownloadsPlugin
from bekas.plugins.rust_target import RustTargetPlugin
from bekas.plugins.screenshots import ScreenshotsPlugin


def _make_ctx(**kwargs):
    return Context(dry_run=True, config=kwargs)


def _mock_entry(path: Path, is_dir: bool = False, stat_side_effect=None, name: str | None = None):
    """Create a mock directory entry."""
    return SimpleNamespace(
        name=name or path.name,
        is_dir=lambda: is_dir,
        stat=lambda: (_ for _ in ()).throw(stat_side_effect) if stat_side_effect else path.stat(),
    )


# ─── Downloads ──────────────────────────────────────────────

class TestDownloadsBranches:
    def test_discover_no_downloads_dir(self):
        """If ~/Downloads doesn't exist, yield nothing."""
        plugin = DownloadsPlugin()
        with patch.object(Path, "home", return_value=Path("/nonexistent_home")):
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_discover_stat_error(self, tmp_path):
        """OSError on stat is skipped."""
        plugin = DownloadsPlugin()
        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        f = downloads / "file.txt"
        f.write_text("x")
        old = datetime.now() - timedelta(days=200)
        import os
        os.utime(f, (old.timestamp(), old.timestamp()))
        mock_ent = _mock_entry(f, is_dir=False, stat_side_effect=OSError("boom"))
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(Path, "iterdir", return_value=[mock_ent]),
        ):
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_discover_directory_skipped(self, tmp_path):
        """Directories inside Downloads are skipped."""
        plugin = DownloadsPlugin()
        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        (downloads / "subdir").mkdir()
        old = datetime.now() - timedelta(days=200)
        import os
        os.utime(downloads / "subdir", (old.timestamp(), old.timestamp()))
        with patch.object(Path, "home", return_value=tmp_path):
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_remove_file_gone(self, tmp_path):
        """remove() returns failure if file doesn't exist."""
        plugin = DownloadsPlugin()
        c = Candidate(
            id="dl:x",
            category="downloads.file",
            size_bytes=1,
            path_or_handle=str(tmp_path / "gone.txt"),
            confidence=Confidence.REVIEW,
            reason="r",
        )
        result = plugin.remove(c, _make_ctx())
        assert result.success is False
        assert "does not exist" in result.log

    def test_remove_exception(self, tmp_path):
        """remove() catches unlink exceptions."""
        plugin = DownloadsPlugin()
        f = tmp_path / "locked.txt"
        f.write_text("x")
        with patch("pathlib.Path.unlink", side_effect=PermissionError("denied")):
            c = Candidate(
                id="dl:x",
                category="downloads.file",
                size_bytes=1,
                path_or_handle=str(f),
                confidence=Confidence.REVIEW,
                reason="r",
            )
            result = plugin.remove(c, _make_ctx())
            assert result.success is False
            assert "denied" in result.log

    def test_quarantine_exception(self, tmp_path):
        """quarantine() catches move_to_quarantine exceptions."""
        plugin = DownloadsPlugin()
        f = tmp_path / "file.txt"
        f.write_text("x")
        with patch("bekas.quarantine.move_to_quarantine", side_effect=RuntimeError("boom")):
            c = Candidate(
                id="dl:x",
                category="downloads.file",
                size_bytes=1,
                path_or_handle=str(f),
                confidence=Confidence.REVIEW,
                reason="r",
            )
            result = plugin.quarantine(c, _make_ctx(), str(tmp_path))
            assert result.success is False
            assert "boom" in result.log


# ─── Screenshots ────────────────────────────────────────────

class TestScreenshotsBranches:
    def test_discover_folder_doesnt_exist(self):
        """Screenshot folder missing → nothing yielded."""
        plugin = ScreenshotsPlugin()
        with patch.object(Path, "home", return_value=Path("/nonexistent")):
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_discover_directory_entry_skipped(self, tmp_path):
        """Directories inside screenshot folder are skipped."""
        plugin = ScreenshotsPlugin()
        home = tmp_path / "home"
        home.mkdir()
        pics = home / "Pictures" / "Screenshots"
        pics.mkdir(parents=True)
        (pics / "subdir").mkdir()
        with patch.object(Path, "home", return_value=home):
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_discover_name_filter(self, tmp_path):
        """Only image-like names match."""
        plugin = ScreenshotsPlugin()
        home = tmp_path / "home"
        home.mkdir()
        pics = home / "Pictures" / "Screenshots"
        pics.mkdir(parents=True)
        old = datetime.now() - timedelta(days=100)
        (pics / "notes.txt").write_text("x")
        (pics / "Screenshot_2023.png").write_text("x")
        # Set mtime to old
        import os
        os.utime(pics / "Screenshot_2023.png", (old.timestamp(), old.timestamp()))
        os.utime(pics / "notes.txt", (old.timestamp(), old.timestamp()))
        with patch.object(Path, "home", return_value=home):
            cands = list(plugin.discover(_make_ctx()))
            assert len(cands) == 1
            assert cands[0].id == "ss:Screenshot_2023.png"

    def test_discover_stat_error(self, tmp_path):
        """OSError on stat skips the entry."""
        plugin = ScreenshotsPlugin()
        home = tmp_path / "home"
        home.mkdir()
        pics = home / "Pictures" / "Screenshots"
        pics.mkdir(parents=True)
        f = pics / "ss.png"
        f.write_text("x")
        old = datetime.now() - timedelta(days=100)
        import os
        os.utime(f, (old.timestamp(), old.timestamp()))
        mock_ent = _mock_entry(f, is_dir=False, stat_side_effect=OSError("boom"), name="ss.png")
        with (
            patch.object(Path, "home", return_value=home),
            patch.object(Path, "iterdir", return_value=[mock_ent]),
        ):
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_remove_file_gone(self, tmp_path):
        """remove() when file is already gone."""
        plugin = ScreenshotsPlugin()
        c = Candidate(
            id="ss:x",
            category="screenshots.file",
            size_bytes=1,
            path_or_handle=str(tmp_path / "gone.png"),
            confidence=Confidence.REVIEW,
            reason="r",
        )
        result = plugin.remove(c, _make_ctx())
        assert result.success is False

    def test_remove_exception(self, tmp_path):
        """remove() catches exceptions."""
        plugin = ScreenshotsPlugin()
        f = tmp_path / "ss.png"
        f.write_text("x")
        with patch("pathlib.Path.unlink", side_effect=PermissionError("denied")):
            c = Candidate(
                id="ss:x",
                category="screenshots.file",
                size_bytes=1,
                path_or_handle=str(f),
                confidence=Confidence.REVIEW,
                reason="r",
            )
            result = plugin.remove(c, _make_ctx())
            assert result.success is False
            assert "denied" in result.log

    def test_quarantine_exception(self, tmp_path):
        """quarantine() catches move_to_quarantine exceptions."""
        plugin = ScreenshotsPlugin()
        f = tmp_path / "ss.png"
        f.write_text("x")
        with patch("bekas.quarantine.move_to_quarantine", side_effect=RuntimeError("boom")):
            c = Candidate(
                id="ss:x",
                category="screenshots.file",
                size_bytes=1,
                path_or_handle=str(f),
                confidence=Confidence.REVIEW,
                reason="r",
            )
            result = plugin.quarantine(c, _make_ctx(), str(tmp_path))
            assert result.success is False


# ─── Dotfiles Backups ───────────────────────────────────────

class TestDotfilesBackupsBranches:
    def test_discover_directory_skipped(self, tmp_path):
        """Directories in home are skipped."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".config").mkdir()
        with patch.object(Path, "home", return_value=home):
            plugin = DotfilesBackupsPlugin()
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_discover_no_dotfiles(self, tmp_path):
        """Non-dotfiles don't match."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "normal.txt").write_text("x")
        with patch.object(Path, "home", return_value=home):
            plugin = DotfilesBackupsPlugin()
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_remove_file_gone(self, tmp_path):
        """remove() when file is gone."""
        plugin = DotfilesBackupsPlugin()
        c = Candidate(
            id="dotfile:x",
            category="dotfiles.backups",
            size_bytes=1,
            path_or_handle=str(tmp_path / "gone.bak"),
            confidence=Confidence.SAFE,
            reason="r",
        )
        result = plugin.remove(c, _make_ctx())
        assert result.success is False
        assert "does not exist" in result.log

    def test_remove_exception(self, tmp_path):
        """remove() catches exceptions."""
        plugin = DotfilesBackupsPlugin()
        f = tmp_path / ".zshrc.bak"
        f.write_text("x")
        with patch("pathlib.Path.unlink", side_effect=PermissionError("denied")):
            c = Candidate(
                id="dotfile:x",
                category="dotfiles.backups",
                size_bytes=1,
                path_or_handle=str(f),
                confidence=Confidence.SAFE,
                reason="r",
            )
            result = plugin.remove(c, _make_ctx())
            assert result.success is False
            assert "denied" in result.log

    def test_quarantine_exception(self, tmp_path):
        """quarantine() catches exceptions."""
        plugin = DotfilesBackupsPlugin()
        f = tmp_path / ".zshrc.bak"
        f.write_text("x")
        with patch("bekas.quarantine.move_to_quarantine", side_effect=RuntimeError("boom")):
            c = Candidate(
                id="dotfile:x",
                category="dotfiles.backups",
                size_bytes=1,
                path_or_handle=str(f),
                confidence=Confidence.SAFE,
                reason="r",
            )
            result = plugin.quarantine(c, _make_ctx(), str(tmp_path))
            assert result.success is False


# ─── Rust Target ────────────────────────────────────────────

class TestRustTargetBranches:
    def test_discover_no_roots(self, tmp_path):
        """If all roots don't exist, yield nothing."""
        home = tmp_path / "home"
        home.mkdir()
        with patch.object(Path, "home", return_value=home):
            plugin = RustTargetPlugin()
            cands = list(plugin.discover(_make_ctx()))
            assert cands == []

    def test_remove_path_gone(self, tmp_path):
        """remove() when target directory is gone."""
        plugin = RustTargetPlugin()
        c = Candidate(
            id="rust:x",
            category="rust.target",
            size_bytes=1,
            path_or_handle=str(tmp_path / "gone"),
            confidence=Confidence.SAFE,
            reason="r",
        )
        result = plugin.remove(c, _make_ctx())
        assert result.success is False
        assert "does not exist" in result.log

    def test_remove_rmtree_exception(self, tmp_path):
        """remove() catches shutil.rmtree exceptions."""
        import shutil

        plugin = RustTargetPlugin()
        d = tmp_path / "target"
        d.mkdir()
        (d / "file.txt").write_text("x")
        with patch.object(shutil, "rmtree", side_effect=PermissionError("denied")):
            c = Candidate(
                id="rust:x",
                category="rust.target",
                size_bytes=1,
                path_or_handle=str(d),
                confidence=Confidence.SAFE,
                reason="r",
            )
            result = plugin.remove(c, _make_ctx())
            assert result.success is False
            assert "denied" in result.log

    def test_quarantine_exception(self, tmp_path):
        """quarantine() catches exceptions."""
        plugin = RustTargetPlugin()
        d = tmp_path / "target"
        d.mkdir()
        (d / "file.txt").write_text("x")
        with patch("bekas.quarantine.move_to_quarantine", side_effect=RuntimeError("boom")):
            c = Candidate(
                id="rust:x",
                category="rust.target",
                size_bytes=1,
                path_or_handle=str(d),
                confidence=Confidence.SAFE,
                reason="r",
            )
            result = plugin.quarantine(c, _make_ctx(), str(tmp_path))
            assert result.success is False
