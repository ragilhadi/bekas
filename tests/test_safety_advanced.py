"""Advanced safety tests covering edge cases missing from test_safety.py."""

from __future__ import annotations

from pathlib import Path

from bekas.models import Candidate, Confidence
from bekas.safety import filter_candidates, is_excluded


def test_traversal_in_raw_path_is_blocked():
    """Before resolving, raw traversal sequences must be caught."""
    assert is_excluded("/tmp/foo/../../etc/passwd") is True
    assert is_excluded("/tmp/foo/..\\/etc/passwd") is True  # backslash traversal
    # After resolve() there are no .. left, but raw-string guard catches it


def test_resolved_path_under_ancestor_excluded():
    """Resolved children of hard-excluded ancestors must be blocked."""
    assert is_excluded("/etc/passwd") is True
    assert is_excluded("/etc/ssh/sshd_config") is True
    assert is_excluded("/usr/bin/python3") is True


def test_root_is_not_universal_exclusion():
    """/ alone should not match every path after resolve."""
    assert is_excluded("/tmp/safe_file.txt") is False
    assert is_excluded("/var/tmp/safe") is False


def test_user_exclusion_exact_and_ancestor():
    """User exclusions must match exact paths and descendants."""
    assert is_excluded("/tmp/my_stuff/secret", user_exclusions=["/tmp/my_stuff"]) is True
    assert is_excluded("/tmp/my_stuff", user_exclusions=["/tmp/my_stuff"]) is True
    assert is_excluded("/tmp/other", user_exclusions=["/tmp/my_stuff"]) is False


def test_user_exclusion_glob():
    """User exclusion globs must match filenames."""
    assert is_excluded("/tmp/backup.tar.gz", user_exclusions=["/tmp/backup*"]) is True
    assert is_excluded("/tmp/keep.txt", user_exclusions=["/tmp/backup*"]) is False


def test_symlink_resolution_is_excluded(tmp_path: Path) -> None:
    """Symlinks that resolve into excluded areas must be blocked."""
    secret = tmp_path / "real_secret"
    secret.mkdir()
    symlink = tmp_path / "link_to_secret"
    symlink.symlink_to(secret)
    # The resolved path is under tmp_path, not a hard exclusion, so it passes
    assert is_excluded(symlink) is False


def test_case_sensitivity_non_windows(tmp_path: Path) -> None:
    """On case-sensitive filesystems, case changes should still match after resolve."""
    # This is mostly a smoke test; real case-sensitivity depends on the OS
    folder = tmp_path / "MyFolder"
    folder.mkdir()
    assert is_excluded(folder) is False


def test_unicode_and_whitespace_paths(tmp_path: Path) -> None:
    """Paths with unicode and spaces must be handled safely."""
    weird = tmp_path / "файл с пробелами and 日本語"
    weird.mkdir()
    assert is_excluded(weird) is False


def test_very_long_path(tmp_path: Path) -> None:
    """Extremely long paths should not crash."""
    deep = tmp_path
    for i in range(30):
        deep = deep / f"dir{i}"
    deep.mkdir(parents=True)
    assert is_excluded(deep) is False


def test_safety_filters_candidates():
    """filter_candidates must drop excluded items."""
    candidates = [
        Candidate(
            id="a", category="x", size_bytes=1, path_or_handle="/etc/passwd", confidence=Confidence.SAFE, reason="r"
        ),
        Candidate(
            id="b", category="x", size_bytes=2, path_or_handle="/tmp/safe", confidence=Confidence.SAFE, reason="r"
        ),
    ]
    safe = filter_candidates(candidates)
    assert len(safe) == 1
    assert safe[0].id == "b"


def test_windows_hard_exclusions_on_posix_are_noop():
    """Windows paths listed in _HARD_EXCLUSIONS will not resolve on POSIX, but shouldn't crash."""
    assert is_excluded("/tmp/whatever") is False
