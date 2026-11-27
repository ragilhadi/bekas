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


def test_new_sensitive_paths_excluded():
    """Every pattern added by the #20 guardrails keeps credential files out of reach."""
    home = str(Path.home())
    assert is_excluded(f"{home}/proj/.env")
    assert is_excluded(f"{home}/proj/.env.production")
    assert is_excluded(f"{home}/.npmrc")
    assert is_excluded(f"{home}/.pypirc")
    assert is_excluded(f"{home}/.docker/config.json")
    assert is_excluded(f"{home}/.config/gh/hosts.yml")
    assert is_excluded(f"{home}/.password-store/git/main")
    assert is_excluded(f"{home}/.ssh/id_ed25519")
    assert is_excluded(f"{home}/keys/id_rsa")
    assert is_excluded(f"{home}/Library/Keychains/login.keychain-db")
    assert is_excluded(f"{home}/.config/google-chrome/Default/Login Data")
    assert is_excluded(f"{home}/.mozilla/firefox/abc/cookies.sqlite")
    assert not is_excluded(f"{home}/Documents/cookies-jar-notes.txt")
