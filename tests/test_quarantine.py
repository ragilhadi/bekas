"""Tests for the quarantine system."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from bekas.quarantine import (
    move_to_quarantine,
    purge_old_quarantine,
    quarantine_path,
    restore_from_quarantine,
)


def test_quarantine_path_creates_dir(tmp_path: Path):
    with patch("bekas.quarantine.quarantine_dir") as mock_dir:
        mock_dir.return_value = tmp_path
        p = quarantine_path()
        assert p.exists()
        assert p.parent == tmp_path


def test_move_to_quarantine(tmp_path: Path):
    with patch("bekas.quarantine.quarantine_dir") as mock_dir:
        mock_dir.return_value = tmp_path
        mock_add = patch("bekas.quarantine.add_quarantine")
        with mock_add:
            original = tmp_path / "original.txt"
            original.write_text("hello")
            dest = move_to_quarantine("run_01", original, "test", 5, {})
            assert dest.exists()
            assert not original.exists()


def test_move_to_quarantine_collision(tmp_path: Path):
    with patch("bekas.quarantine.quarantine_dir") as mock_dir:
        mock_dir.return_value = tmp_path
        mock_add = patch("bekas.quarantine.add_quarantine")
        with mock_add:
            # Pre-create a file with same name in quarantine
            (tmp_path / "foo.txt").write_text("collision")
            original = tmp_path / "original.txt"
            original.write_text("hello")
            dest = move_to_quarantine("run_01", original, "test", 5, {})
            assert dest != tmp_path / "foo.txt"
            assert dest.exists()


def test_restore_from_quarantine(tmp_path: Path):
    with patch("bekas.quarantine.quarantine_dir") as mock_dir:
        mock_dir.return_value = tmp_path
        mock_add = patch("bekas.quarantine.add_quarantine")
        with mock_add:
            original = tmp_path / "to_restore.txt"
            original.write_text("data")
            dest = move_to_quarantine("run_01", original, "test", 4, {})
            with patch("bekas.quarantine.list_quarantine") as mock_list:
                mock_list.return_value = [
                    {
                        "quarantine_id": "q_abc123",
                        "quarantine_path": str(dest),
                        "original_path": str(original),
                    }
                ]
                with patch("bekas.quarantine.remove_quarantine_entry") as mock_remove:
                    restored = restore_from_quarantine("q_abc123")
                    assert restored == original
                    assert original.exists()
                    assert not dest.exists()
                    mock_remove.assert_called_once_with("q_abc123")


def test_restore_from_quarantine_by_path(tmp_path: Path):
    """Restore lookup by quarantine_path works too."""
    with patch("bekas.quarantine.list_quarantine") as mock_list:
        mock_list.return_value = [
            {
                "quarantine_id": "q_abc123",
                "quarantine_path": str(tmp_path / "x.txt"),
                "original_path": str(tmp_path / "orig.txt"),
            }
        ]
        with (
            patch("bekas.quarantine.remove_quarantine_entry") as mock_remove,
            patch("pathlib.Path.exists") as mock_exists,
        ):
            mock_exists.return_value = False
            with patch("shutil.move"):
                restore_from_quarantine(str(tmp_path / "x.txt"))


def test_purge_old_quarantine(tmp_path: Path):
    with patch("bekas.quarantine.quarantine_dir") as mock_dir:
        mock_dir.return_value = tmp_path
        mock_add = patch("bekas.quarantine.add_quarantine")
        with mock_add:
            old = tmp_path / "old.txt"
            old.write_text("old")
            dest = move_to_quarantine("run_01", old, "test", 3, {})
            with patch("bekas.quarantine.list_quarantine") as mock_list:
                mock_list.return_value = [
                    {
                        "quarantine_id": "q_abc123",
                        "quarantine_path": str(dest),
                        "original_path": str(old),
                        "timestamp": (datetime.now(UTC) - timedelta(days=100)).isoformat(),
                        "size_bytes": 3,
                    }
                ]
                with patch("bekas.quarantine.remove_quarantine_entry") as mock_remove:
                    removed, freed = purge_old_quarantine(retention_days=30)
                    assert removed == 1
                    assert freed == 3
                    mock_remove.assert_called_once_with("q_abc123")
