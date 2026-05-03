"""Tests for safety module."""

from pathlib import Path

from bekas.safety import is_excluded


def test_hard_exclusion_root():
    assert is_excluded("/etc/passwd")


def test_hard_exclusion_home_sensitive():
    home = str(Path.home())
    assert is_excluded(f"{home}/.ssh/id_rsa")
    assert is_excluded(f"{home}/.gnupg")
    assert not is_excluded(f"{home}/Documents/foo.txt")


def test_user_exclusion():
    home = str(Path.home())
    assert is_excluded(f"{home}/work/secret-project", user_exclusions=["~/work/secret-project"])


def test_traversal_excluded():
    assert is_excluded("/foo/../../../etc/passwd")
