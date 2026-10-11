"""Regression tests for quarantine exclusion, undo reporting and plugin toggling."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import yaml

from bekas.cli import cli
from bekas.config import is_plugin_enabled as _real_is_plugin_enabled
from bekas.config import set_plugin_enabled
from bekas.locking import AlreadyRunningError, ProcessLock
from bekas.safety import is_excluded


@pytest.fixture(autouse=True)
def _real_plugin_matching(monkeypatch):
    """conftest stubs is_plugin_enabled to True; these tests need the real matcher."""
    monkeypatch.setattr("bekas.config.is_plugin_enabled", _real_is_plugin_enabled)


NAMES = ["docker.images", "python.cache", "uv.cache", "node.modules"]


def test_bekas_data_dir_is_excluded(tmp_path):
    data = tmp_path / "data"
    item = data / "quarantine" / "20260101_000000" / "node_modules"
    item.mkdir(parents=True)
    with patch("bekas.config._data_dir", return_value=data):
        assert is_excluded(item) is True
        assert is_excluded(data) is True


def test_path_outside_data_dir_not_excluded_by_data_dir_rule(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    with patch("bekas.config._data_dir", return_value=data):
        assert is_excluded(other) is False


def _write_cfg(path, patterns):
    path.write_text(
        yaml.safe_dump({"active_profile": "default", "profiles": {"default": {"enabled_plugins": patterns}}})
    )


def test_enable_plugin_appends(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, ["python.*"])
    with patch("bekas.config.ensure_config", return_value=cfg):
        changed, _ = set_plugin_enabled("uv.cache", True, NAMES)
    assert changed
    assert "uv.cache" in yaml.safe_load(cfg.read_text())["profiles"]["default"]["enabled_plugins"]


def test_enable_already_enabled_is_noop(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, ["*"])
    with patch("bekas.config.ensure_config", return_value=cfg):
        changed, msg = set_plugin_enabled("uv.cache", True, NAMES)
    assert not changed
    assert "already enabled" in msg


def test_disable_expands_wildcard(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, ["*"])
    with patch("bekas.config.ensure_config", return_value=cfg):
        changed, _ = set_plugin_enabled("uv.cache", False, NAMES)
    assert changed
    pats = yaml.safe_load(cfg.read_text())["profiles"]["default"]["enabled_plugins"]
    assert "uv.cache" not in pats
    assert set(pats) == set(NAMES) - {"uv.cache"}


def test_disable_exact_entry(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, ["uv.cache", "python.*"])
    with patch("bekas.config.ensure_config", return_value=cfg):
        changed, _ = set_plugin_enabled("uv.cache", False, NAMES)
    assert changed
    assert yaml.safe_load(cfg.read_text())["profiles"]["default"]["enabled_plugins"] == ["python.*"]


def test_unknown_plugin_and_profile(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_cfg(cfg, ["*"])
    with patch("bekas.config.ensure_config", return_value=cfg):
        assert set_plugin_enabled("nope", True, NAMES)[0] is False
        assert "Profile not found" in set_plugin_enabled("uv.cache", False, NAMES, "ghost")[1]


def test_undo_reports_permanent_deletions(runner):
    run = {
        "run_id": "run_x",
        "results_json": json.dumps(
            [
                {"candidate_id": "a", "success": True, "undo_token": None, "log": "deleted"},
                {"candidate_id": "b", "success": True, "undo_token": "q_1", "log": "quarantined"},
            ]
        ),
    }
    with (
        patch("bekas.cli.get_run", return_value=run),
        patch("bekas.quarantine.restore_from_quarantine"),
        patch("bekas.quarantine.mark_undo_restored"),
    ):
        result = runner.invoke(cli, ["undo", "run_x"])
    assert "Restored 1 item(s)" in result.output
    assert "1 item(s) were deleted permanently" in result.output


def test_lock_contention_and_stale_file(tmp_path):
    lock_file = tmp_path / "bekas.lock"
    lock_file.write_text("1")  # stale file naming a live PID must not block
    first = ProcessLock(lock_file)
    first.acquire()
    try:
        second = ProcessLock(lock_file)
        try:
            second.acquire()
        except AlreadyRunningError as exc:
            assert exc.pid > 0
        else:
            raise AssertionError("second acquire should have failed")
    finally:
        first.release()
    ProcessLock(lock_file).acquire()
