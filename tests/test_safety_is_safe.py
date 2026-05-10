"""Tests for safety module — is_safe_to_delete and edge cases."""

from pathlib import Path

from bekas.safety import (
    _is_inside_active_venv,
    _is_non_local_filesystem,
    _is_symlink_outside_home,
    _is_too_recent,
    filter_candidates,
    is_excluded,
    is_safe_to_delete,
)

# --- is_safe_to_delete layer tests ---


def test_safe_normal_path(tmp_path):
    f = tmp_path / "safe.txt"
    f.write_text("hello")
    safe, reason = is_safe_to_delete(f)
    assert safe is True
    assert reason == ""


def test_unsafe_traversal():
    safe, reason = is_safe_to_delete("/tmp/foo/../../etc/passwd")
    assert safe is False
    assert "traversal" in reason.lower()


def test_unsafe_system_path():
    safe, reason = is_safe_to_delete("/etc/passwd")
    assert safe is False
    assert "hard-excluded" in reason.lower()


def test_safe_non_sensitive_path(tmp_path):
    f = tmp_path / "myfile.txt"
    f.write_text("x")
    safe, reason = is_safe_to_delete(f)
    assert safe is True


def test_unsafe_recent_file(tmp_path):
    f = tmp_path / "recent.txt"
    f.write_text("x")
    safe, reason = is_safe_to_delete(f, min_quiet_hours=1)
    assert safe is False
    assert "modified within" in reason.lower()


def test_unsafe_inside_active_venv(tmp_path, monkeypatch):
    venv = tmp_path / "myenv"
    venv.mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    f = venv / "lib" / "python" / "site.py"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    safe, reason = is_safe_to_delete(f)
    assert safe is False
    assert "virtual environment" in reason.lower()


def test_unsafe_user_exclusion(tmp_path):
    f = tmp_path / "secret" / "project"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    safe, reason = is_safe_to_delete(f, user_exclusions=[str(tmp_path / "secret")])
    assert safe is False
    assert "user exclusion" in reason.lower()


# --- filter_candidates ---


def test_filter_candidates_drops_excluded():
    from bekas.models import Candidate, Confidence

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


# --- _is_too_recent ---


def test_is_too_recent_zero_hours(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert _is_too_recent(f, min_quiet_hours=0) is False  # 0 threshold always passes


def test_is_too_recent_with_threshold(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert _is_too_recent(f, min_quiet_hours=1) is True


# --- _is_symlink_outside_home ---


def test_symlink_inside_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    target = home / "real"
    target.mkdir()
    link = home / "link"
    link.symlink_to(target)
    assert _is_symlink_outside_home(link) is False


# --- _is_non_local_filesystem ---


def test_non_local_with_no_psutil(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert _is_non_local_filesystem(f) is False


# --- _is_inside_active_venv ---


def test_inside_active_venv(tmp_path, monkeypatch):
    venv = tmp_path / "env"
    venv.mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    f = venv / "lib" / "x.py"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    assert _is_inside_active_venv(f) is True
    assert _is_inside_active_venv(tmp_path / "outside.py") is False


def test_inside_conda_prefix(tmp_path, monkeypatch):
    conda = tmp_path / "conda"
    conda.mkdir()
    monkeypatch.setenv("CONDA_PREFIX", str(conda))
    assert _is_inside_active_venv(conda / "bin" / "python") is True


# --- is_excluded edge cases ---


def test_is_excluded_nonexistent_path():
    assert is_excluded("/nonexistent/path/that/does/not/exist") is False  # only system paths excluded


def test_is_excluded_user_glob_star(tmp_path):
    f = tmp_path / "foo" / "bar" / "baz"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    assert is_excluded(f, user_exclusions=[str(tmp_path / "foo")]) is True


def test_is_excluded_windows_paths_on_posix():
    # Windows paths in _HARD_EXCLUSIONS won't resolve on POSIX but shouldn't crash
    assert is_excluded(r"C:\Windows\System32") is False  # unresolved windows path → no match
