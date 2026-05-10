"""Tests for safety.py exception and edge-case branches."""

from unittest.mock import patch

from bekas.safety import (
    _expand_exclusions,
    _is_non_local_filesystem,
    _is_symlink_outside_home,
    is_excluded,
    is_safe_to_delete,
)


def test_expand_exclusions_runtime_error(monkeypatch):
    """If Path.home() raises RuntimeError, _expand_exclusions should not crash."""
    with patch("bekas.safety.Path.home", side_effect=RuntimeError("no home")):
        excl = _expand_exclusions()
        assert isinstance(excl, list)


def test_is_non_local_filesystem_import_error(tmp_path):
    """If psutil is not importable, _is_non_local_filesystem should return False."""
    f = tmp_path / "file.txt"
    f.write_text("x")
    with patch.dict("sys.modules", {"psutil": None}):
        assert _is_non_local_filesystem(f) is False


def test_is_symlink_outside_home_oserror(tmp_path, monkeypatch):
    """If path.is_symlink() raises OSError, treat as True for safety."""
    f = tmp_path / "link"
    f.write_text("x")
    with patch("pathlib.Path.is_symlink", side_effect=OSError("broken")):
        assert _is_symlink_outside_home(f) is True


def test_is_excluded_path_resolve_fails():
    """If path.resolve() fails, is_excluded should return True."""
    with patch("pathlib.Path.resolve", side_effect=OSError("cannot resolve")):
        assert is_excluded("/tmp/foo") is True


def test_is_safe_to_delete_path_resolve_fails(tmp_path):
    """If path.resolve() fails inside is_safe_to_delete."""
    with patch("pathlib.Path.resolve", side_effect=OSError("boom")):
        f = tmp_path / "file.txt"
        f.write_text("x")
        safe, reason = is_safe_to_delete(f)
        assert safe is False
        assert "unable to resolve" in reason.lower()
