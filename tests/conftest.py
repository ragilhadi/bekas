"""Shared pytest fixtures for CLI tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _patch_data_dir(monkeypatch):
    """Redirect data and config dirs into a temporary path for CLI tests."""
    import bekas.clean as clean_mod
    import bekas.cli as cli_mod
    import bekas.config as cfg
    import bekas.locking as locking_mod
    import bekas.runner as runner_mod

    tmp = tempfile.mkdtemp()
    ddir = Path(tmp) / "data"
    cdir = Path(tmp) / "config"
    ddir.mkdir(parents=True, exist_ok=True)
    cdir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cfg, "_data_dir", lambda: ddir)
    monkeypatch.setattr(cfg, "_config_dir", lambda: cdir)

    _permissive = {"interactive": False, "quarantine_enabled": False, "enabled_plugins": ["*"]}
    _permissive_cfg = {"active_profile": "default", "profiles": {"default": _permissive}}
    for mod in (cfg, cli_mod, runner_mod, clean_mod):
        if hasattr(mod, "load_config"):
            monkeypatch.setattr(mod, "load_config", lambda: _permissive_cfg)
        if hasattr(mod, "is_plugin_enabled"):
            monkeypatch.setattr(mod, "is_plugin_enabled", lambda patterns, name: True)
        if hasattr(mod, "profile_for"):
            monkeypatch.setattr(mod, "profile_for", lambda n=None: _permissive)
    monkeypatch.setattr(cli_mod, "ensure_config", lambda: cdir / "config.yaml")
    monkeypatch.setattr(
        locking_mod, "acquire_lock", lambda lock_file=None: locking_mod.ProcessLock(lock_file or ddir / ".test.lock")
    )


@pytest.fixture
def runner():
    return CliRunner()
