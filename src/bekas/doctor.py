"""Diagnostic command for bekas — checks the runtime environment."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil
import yaml

from bekas.config import config_path, ensure_config, quarantine_dir, runs_db_path
from bekas.plugin import discover_plugins


class CheckResult:
    """Result of a single doctor check.

    Attributes:
        name: Machine-readable check identifier.
        status: One of ``"pass"``, ``"fail"``, ``"warn"``.
        message: Human-readable description of the result.
        detail: Optional extra information (paths, versions, etc.).
    """

    def __init__(self, name: str, status: str, message: str, detail: str = "") -> None:
        """Initialize a check result.

        Args:
            name: Check identifier.
            status: ``"pass"``, ``"fail"``, or ``"warn"``.
            message: Human-readable result message.
            detail: Optional additional detail.
        """
        self.name = name
        self.status = status
        self.message = message
        self.detail = detail


def _check_python_version() -> CheckResult:
    """Check that the Python version is >= 3.11.

    Returns:
        CheckResult indicating pass or fail.
    """
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        return CheckResult("python_version", "pass", f"Python {major}.{minor}.{sys.version_info.micro}")
    return CheckResult("python_version", "fail", f"Python {major}.{minor} — requires >= 3.11")


def _check_config() -> CheckResult:
    """Check that the config file exists, parses, and is schema-valid.

    Returns:
        CheckResult indicating pass, fail, or warn.
    """
    cfg_path = config_path()
    if not cfg_path.exists():
        try:
            ensure_config()
        except Exception as exc:
            return CheckResult("config", "fail", f"Config missing and could not create: {exc}")
    try:
        raw = cfg_path.read_text()
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            return CheckResult("config", "fail", f"Config at {cfg_path} is not a YAML mapping")
        if "profiles" not in data or not isinstance(data["profiles"], dict):
            return CheckResult("config", "warn", f"Config at {cfg_path} missing 'profiles' key")
        return CheckResult("config", "pass", "Config OK", str(cfg_path))
    except Exception as exc:
        return CheckResult("config", "fail", f"Config parse error: {exc}")


def _check_quarantine() -> CheckResult:
    """Check that the quarantine directory exists, is writable, and on the same FS as $HOME.

    Returns:
        CheckResult indicating pass, fail, or warn.
    """
    try:
        qdir = quarantine_dir()
        test_file = qdir / ".bekas_write_test"
        test_file.write_text("ok")
        test_file.unlink()
    except Exception as exc:
        return CheckResult("quarantine", "fail", f"Quarantine not writable: {exc}")

    try:
        home_stat = Path.home().stat()
        qdir_stat = qdir.stat()
        if home_stat.st_dev != qdir_stat.st_dev:
            return CheckResult(
                "quarantine",
                "warn",
                "Quarantine on different filesystem than $HOME (slower restores)",
                str(qdir),
            )
    except (OSError, RuntimeError):
        pass

    return CheckResult("quarantine", "pass", "Quarantine writable", str(qdir))


def _check_undo_db() -> CheckResult:
    """Check that the SQLite undo database opens and has the expected schema.

    Returns:
        CheckResult indicating pass or fail.
    """
    import sqlite3

    db_path = runs_db_path()
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        required = {"runs", "quarantine"}
        missing = required - tables
        if missing:
            return CheckResult("undo_db", "warn", f"Undo DB missing tables: {missing}", str(db_path))
        return CheckResult("undo_db", "pass", "Undo DB schema OK", str(db_path))
    except Exception as exc:
        return CheckResult("undo_db", "fail", f"Undo DB error: {exc}", str(db_path))


def _check_docker() -> CheckResult:
    """Check that the Docker CLI is available and responsive.

    Returns:
        CheckResult indicating pass or fail.
    """
    try:
        result = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return CheckResult("docker", "pass", "Docker CLI available")
        return CheckResult("docker", "fail", "Docker CLI found but 'docker version' failed")
    except FileNotFoundError:
        return CheckResult("docker", "fail", "Docker CLI not found (plugin docker.images will be skipped)")
    except subprocess.TimeoutExpired:
        return CheckResult("docker", "fail", "Docker CLI timed out after 2s")


def _check_git() -> CheckResult:
    """Check that the Git CLI is available.

    Returns:
        CheckResult indicating pass or fail.
    """
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return CheckResult("git", "pass", f"Git {result.stdout.strip()}")
        return CheckResult("git", "fail", "Git CLI found but 'git --version' failed")
    except FileNotFoundError:
        return CheckResult("git", "fail", "Git CLI not found (plugin git.branches will be skipped)")
    except subprocess.TimeoutExpired:
        return CheckResult("git", "fail", "Git CLI timed out after 2s")


def _check_plugins() -> CheckResult:
    """Check that every discovered plugin can be imported without error.

    Returns:
        CheckResult indicating pass, fail, or warn.
    """
    try:
        plugins = discover_plugins()
        return CheckResult("plugins", "pass", f"{len(plugins)} plugins loaded")
    except Exception as exc:
        return CheckResult("plugins", "fail", f"Plugin discovery failed: {exc}")


def _check_disk_space() -> CheckResult:
    """Check that the quarantine filesystem has > 1 GB free.

    Returns:
        CheckResult indicating pass or warn.
    """
    try:
        qdir = quarantine_dir()
        usage = psutil.disk_usage(str(qdir))
        free_gb = usage.free / (1024**3)
        if free_gb > 1:
            return CheckResult("disk_space", "pass", f"{free_gb:.1f} GB free on quarantine FS")
        return CheckResult("disk_space", "warn", f"Only {free_gb:.1f} GB free on quarantine FS")
    except Exception as exc:
        return CheckResult("disk_space", "warn", f"Could not check disk space: {exc}")


def _check_cross_fs() -> CheckResult:
    """Warn if quarantine is on a different device than typical scan roots.

    Returns:
        CheckResult indicating pass or warn.
    """
    try:
        qdir = quarantine_dir()
        q_dev = qdir.stat().st_dev
        for root in (Path.home(), Path("/tmp")):
            try:
                if root.exists() and root.stat().st_dev != q_dev:
                    return CheckResult(
                        "cross_fs",
                        "warn",
                        f"Quarantine on different filesystem than {root} (slower restores)",
                    )
            except OSError:
                continue
        return CheckResult("cross_fs", "pass", "Quarantine on same filesystem as scan roots")
    except Exception as exc:
        return CheckResult("cross_fs", "warn", f"Could not check cross-FS: {exc}")


# Ordered list of all checks
default_checks: list[Any] = [
    _check_python_version,
    _check_config,
    _check_quarantine,
    _check_undo_db,
    _check_docker,
    _check_git,
    _check_plugins,
    _check_disk_space,
    _check_cross_fs,
]


def run_checks(skip: list[str] | None = None) -> list[CheckResult]:
    """Run all doctor checks and return their results.

    Args:
        skip: Optional list of check names to skip.

    Returns:
        List of :class:`CheckResult` in the order the checks are defined.
    """
    skip_set = set(skip or [])
    results: list[CheckResult] = []
    for check_fn in default_checks:
        derived_name = check_fn.__name__.replace("_check_", "")
        if derived_name in skip_set:
            continue
        try:
            results.append(check_fn())
        except Exception as exc:
            results.append(CheckResult(derived_name, "fail", f"Check crashed: {exc}"))
    return results


def format_human(results: list[CheckResult]) -> str:
    """Format doctor results as human-readable text.

    Args:
        results: List of check results.

    Returns:
        Multi-line human-readable string with Unicode indicators.
    """
    lines: list[str] = []
    for r in results:
        icon = "✓" if r.status == "pass" else "✗" if r.status == "fail" else "⚠"
        detail = f"  ({r.detail})" if r.detail else ""
        lines.append(f"  {icon} {r.message}{detail}")
    errors = sum(1 for r in results if r.status == "fail")
    warnings = sum(1 for r in results if r.status == "warn")
    summary_parts: list[str] = []
    if errors:
        summary_parts.append(f"{errors} error{'s' if errors > 1 else ''}")
    if warnings:
        summary_parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")
    if summary_parts:
        lines.append("")
        lines.append(" · ".join(summary_parts))
    return "\n".join(lines)


def format_json(results: list[CheckResult]) -> str:
    """Format doctor results as JSON.

    Args:
        results: List of check results.

    Returns:
        Indented JSON string.
    """
    data = [{"check": r.name, "status": r.status, "message": r.message, "detail": r.detail} for r in results]
    return json.dumps(data, indent=2)


def exit_code(results: list[CheckResult]) -> int:
    """Compute the appropriate exit code from doctor results.

    Returns:
        0 if no errors (warnings are OK), 1 if any errors.
    """
    return 1 if any(r.status == "fail" for r in results) else 0
