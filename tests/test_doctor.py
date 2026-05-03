"""Tests for the doctor diagnostic command (P1.3)."""

from __future__ import annotations

import json as json_mod

from bekas.doctor import CheckResult, exit_code, format_human, format_json, run_checks


def test_doctor_python_version():
    """Python version check passes."""
    results = run_checks(skip=["docker", "git", "plugins", "disk_space", "cross_fs", "quarantine", "undo_db", "config"])
    py_check = next((r for r in results if r.name == "python_version"), None)
    assert py_check is not None
    assert py_check.status == "pass"


def test_doctor_format_human():
    """Human formatter produces expected icons."""
    results = [
        CheckResult("a", "pass", "ok", "/path"),
        CheckResult("b", "fail", "bad"),
        CheckResult("c", "warn", "meh"),
    ]
    text = format_human(results)
    assert "✓" in text
    assert "✗" in text
    assert "⚠" in text
    assert "1 error" in text
    assert "1 warning" in text


def test_doctor_format_json():
    """JSON formatter produces valid JSON."""
    results = [CheckResult("a", "pass", "ok")]
    data = format_json(results)
    parsed = json_mod.loads(data)
    assert parsed[0]["check"] == "a"
    assert parsed[0]["status"] == "pass"


def test_doctor_exit_code():
    """Exit code is 1 on failure, 0 otherwise."""
    assert exit_code([CheckResult("a", "pass", "ok")]) == 0
    assert exit_code([CheckResult("a", "fail", "bad")]) == 1
    assert exit_code([CheckResult("a", "warn", "meh")]) == 0


def test_doctor_skip_check():
    """Skipped checks do not appear."""
    results = run_checks(skip=["python_version"])
    assert not any(r.name == "python_version" for r in results)
