"""Configuration management for bekas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir, user_data_dir

DEFAULT_CONFIG = """\
# bekas configuration

active_profile: default

exclude: []

profiles:
  default:
    safety_threshold: safe
    interactive: true
    quarantine_enabled: true
    quarantine_retention_days: 30
    enabled_plugins:
      - "docker.*"
      - "python.*"
      - "node.modules"
      - "rust.target"
      - "downloads"
      - "screenshots"
      - "dotfiles.backups"
    git_repos: []
    plugin_settings:
      python.venvs:
        min_idle_days: 90
      docker.images:
        keep_tagged_for_days: 30
      downloads:
        min_age_days: 180

  aggressive:
    safety_threshold: review
    interactive: false
    quarantine_enabled: true
    enabled_plugins: ["*"]

  ci-runner:
    safety_threshold: safe
    interactive: false
    quarantine_enabled: false
    enabled_plugins:
      - "docker.*"
      - "python.cache"
      - "node.modules"
      - "rust.target"
      - "system.tmp"

git_repos: []
"""


def _config_dir() -> Path:
    return Path(user_config_dir("bekas", appauthor=False))


def _data_dir() -> Path:
    return Path(user_data_dir("bekas", appauthor=False))


def config_path() -> Path:
    return _config_dir() / "config.yaml"


def data_dir() -> Path:
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def quarantine_dir() -> Path:
    d = data_dir() / "quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runs_db_path() -> Path:
    return data_dir() / "runs.db"


def audit_log_path() -> Path:
    return data_dir() / "audit.log"


def ensure_config() -> Path:
    """Create default config if missing. Returns path to config."""
    cfg = config_path()
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(DEFAULT_CONFIG)
    return cfg


def load_config() -> dict[str, Any]:
    cfg = ensure_config()
    with cfg.open() as f:
        return yaml.safe_load(f) or {}


def active_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    name = cfg.get("active_profile", "default")
    profiles = cfg.get("profiles", {})
    return profiles.get(name, {}) or {}


def is_plugin_enabled(patterns: list[str], plugin_name: str) -> bool:
    for pat in patterns:
        if pat == "*":
            return True
        if fnmatch.fnmatch(plugin_name, pat):
            return True
    return False


def profile_for(profile_name: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    if profile_name:
        profiles = cfg.get("profiles", {})
        return profiles.get(profile_name, {}) or {}
    return active_profile(cfg)


import fnmatch  # noqa: E402
