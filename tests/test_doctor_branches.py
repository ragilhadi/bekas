"""Branch coverage tests for doctor.py — error paths, edge cases, formatters."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from bekas.doctor import (
    CheckResult,
    _check_config,
    _check_cross_fs,
    _check_disk_space,
    _check_docker,
    _check_git,
    _check_plugins,
    _check_python_version,
    _check_quarantine,
    _check_undo_db,
    exit_code,
    format_human,
    format_json,
    run_checks,
)


def test_check_python_version_old_python():
    """Simulate Python < 3.11."""
    with patch("sys.version_info", (3, 9, 7)):
        result = _check_python_version()
        assert result.status == "fail"
        assert "3.9" in result.message


def test_check_config_missing_create_fails(monkeypatch):
    """Config missing and ensure_config raises."""
    with patch("bekas.doctor.config_path") as mock_path:
        mock_path.return_value = Path("/nonexistent/bekas/config.yaml")
        with patch("bekas.doctor.ensure_config", side_effect=OSError("no write")):
            result = _check_config()
            assert result.status == "fail"
            assert "could not create" in result.message


def test_check_config_invalid_yaml(tmp_path):
    """Config exists but is not valid YAML mapping."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("not yaml {{{")
    with patch("bekas.doctor.config_path", return_value=cfg):
        result = _check_config()
        assert result.status in ("fail", "warn")


def test_check_config_missing_profiles(tmp_path):
    """Config is a dict but missing 'profiles' key."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("version: '0.2.0'\nactive_profile: default\n")
    with patch("bekas.doctor.config_path", return_value=cfg):
        result = _check_config()
        assert result.status == "warn"
        assert "missing" in result.message.lower() or "profiles" in result.message


def test_check_quarantine_not_writable(tmp_path):
    """Quarantine directory not writable."""
    qdir = tmp_path / "readonly_q"
    qdir.mkdir()
    qdir.chmod(0o555)
    try:
        with patch("bekas.doctor.quarantine_dir", return_value=qdir):
            result = _check_quarantine()
            assert result.status == "fail"
            assert "not writable" in result.message.lower()
    finally:
        qdir.chmod(0o755)


def test_check_quarantine_cross_fs(tmp_path, monkeypatch):
    """Quarantine on different filesystem than $HOME."""
    qdir = tmp_path / "q"
    qdir.mkdir()
    with (
        patch("bekas.doctor.quarantine_dir", return_value=qdir),
        patch("bekas.doctor.Path.home", return_value=tmp_path),
    ):
        # Mock stat to simulate different st_dev
        fake_home_stat = type("S", (), {"st_dev": 1})()
        fake_qdir_stat = type("S", (), {"st_dev": 2})()
        with (
            patch.object(Path, "stat", side_effect=[fake_qdir_stat, fake_home_stat]),
        ):
            result = _check_quarantine()
            assert result.status == "warn"
            assert "different filesystem" in result.message.lower()


def test_check_undo_db_missing_tables(tmp_path):
    """Undo DB opens but is missing required tables."""
    db = tmp_path / "undo.sqlite"
    import sqlite3

    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE runs (id TEXT)")
    conn.commit()
    conn.close()
    with patch("bekas.doctor.runs_db_path", return_value=db):
        result = _check_undo_db()
        assert result.status == "warn"
        assert "missing" in result.message.lower() or "tables" in result.message.lower()


def test_check_undo_db_error():
    """Undo DB path is unreadable."""
    with patch("bekas.doctor.runs_db_path", return_value=Path("/dev/null/bad")):
        result = _check_undo_db()
        assert result.status == "fail"
        assert "error" in result.message.lower()


def test_check_docker_version_fails():
    """Docker CLI found but 'docker version' fails."""
    fake_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
    with patch("subprocess.run", return_value=fake_result):
        result = _check_docker()
        assert result.status == "fail"
        assert "docker" in result.message.lower()


def test_check_docker_timeout():
    """Docker CLI times out."""
    from subprocess import TimeoutExpired

    with patch("subprocess.run", side_effect=TimeoutExpired("docker", 2)):
        result = _check_docker()
        assert result.status == "fail"
        assert "timed out" in result.message.lower()


def test_check_git_version_fails():
    """Git CLI found but 'git --version' fails."""
    fake_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
    with patch("subprocess.run", return_value=fake_result):
        result = _check_git()
        assert result.status == "fail"
        assert "git" in result.message.lower()


def test_check_git_timeout():
    """Git CLI times out."""
    from subprocess import TimeoutExpired

    with patch("subprocess.run", side_effect=TimeoutExpired("git", 2)):
        result = _check_git()
        assert result.status == "fail"
        assert "timed out" in result.message.lower()


def test_check_plugins_discovery_fails():
    """Plugin discovery raises an exception."""
    with patch("bekas.doctor.discover_plugins", side_effect=RuntimeError("boom")):
        result = _check_plugins()
        assert result.status == "fail"
        assert "Plugin discovery failed" in result.message


def test_check_disk_space_low():
    """Less than 1 GB free on quarantine FS."""
    fake_usage = type("U", (), {"free": 500 * 1024 * 1024})()
    with (
        patch("bekas.doctor.quarantine_dir", return_value=Path("/tmp")),
        patch("bekas.doctor.psutil.disk_usage", return_value=fake_usage),
    ):
        result = _check_disk_space()
        assert result.status == "warn"
        assert "Only" in result.message


def test_check_disk_space_exception():
    """Disk usage check raises."""
    with (
        patch("bekas.doctor.quarantine_dir", side_effect=RuntimeError("boom")),
        patch("bekas.doctor.psutil.disk_usage", side_effect=OSError("boom")),
    ):
        result = _check_disk_space()
        assert result.status == "warn"
        assert "Could not check" in result.message


def test_check_cross_fs_exception():
    """Cross-FS check raises an exception."""
    with patch("bekas.doctor.quarantine_dir", side_effect=RuntimeError("boom")):
        result = _check_cross_fs()
        assert result.status == "warn"
        assert "Could not check cross-FS" in result.message


def test_run_checks_skips():
    """run_checks respects skip list."""
    results = run_checks(skip=["python_version", "config", "quarantine", "undo_db", "docker", "git", "plugins", "disk_space", "cross_fs"])
    assert len(results) == 0


def test_run_checks_crash_catcher():
    """If a check crashes, run_checks records a fail."""

    def fake_check():
        raise RuntimeError("boom")

    fake_check.__name__ = "_check_python_version"
    with patch("bekas.doctor.default_checks", [fake_check]):
        results = run_checks()
        py_check = next((r for r in results if r.name == "python_version"), None)
        assert py_check is not None
        assert py_check.status == "fail"
        assert "crashed" in py_check.message


def test_format_human_no_issues():
    """Human formatter with no errors or warnings."""
    results = [CheckResult("a", "pass", "ok")]
    text = format_human(results)
    assert "✓" in text
    assert "error" not in text.lower()
    assert "warning" not in text.lower()


def test_format_human_multiple_errors_and_warnings():
    """Pluralization in summary."""
    results = [
        CheckResult("a", "fail", "bad1"),
        CheckResult("b", "fail", "bad2"),
        CheckResult("c", "warn", "meh1"),
        CheckResult("d", "warn", "meh2"),
    ]
    text = format_human(results)
    assert "2 errors" in text
    assert "2 warnings" in text


def test_format_json_structure():
    """JSON formatter produces expected schema."""
    results = [CheckResult("x", "pass", "ok", "/tmp")]
    data = json.loads(format_json(results))
    assert data[0]["check"] == "x"
    assert data[0]["status"] == "pass"
    assert data[0]["detail"] == "/tmp"


def test_exit_code_only_warn():
    """Warnings alone yield exit 0."""
    assert exit_code([CheckResult("w", "warn", "meh")]) == 0
