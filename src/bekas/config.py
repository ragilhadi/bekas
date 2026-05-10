"""Pydantic-validated configuration for bekas."""

from __future__ import annotations

from pathlib import Path

import yaml
from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, Field, ValidationError


class PluginSettings(BaseModel):
    """Per-plugin configuration settings.

    Attributes:
        min_age_days: Minimum age in days for a candidate to be considered.
        min_idle_days: Minimum idle days for a candidate to be considered.
        extra: Additional plugin-specific key-value settings.
    """

    min_age_days: int | None = None
    min_idle_days: int | None = None
    extra: dict[str, object] = Field(default_factory=dict)


class Profile(BaseModel):
    """A bekas configuration profile.

    Attributes:
        enabled_plugins: List of plugin name patterns (glob) to enable.
        quarantine_enabled: Whether quarantine is active.
        quarantine_retention_days: Days to retain quarantined items.
        plugin_settings: Per-plugin settings keyed by plugin name.
        git_repos: List of git repository paths to scan.
        min_quiet_hours: Minimum hours since last modification.
        plugin_timeout_seconds: Hard timeout per plugin during audit.
    """

    enabled_plugins: list[str] = ["*"]
    quarantine_enabled: bool = True
    quarantine_retention_days: int = 30
    plugin_settings: dict[str, PluginSettings] = {}
    git_repos: list[str] = []
    min_quiet_hours: int = 6
    plugin_timeout_seconds: int = 60
    safety_threshold: str = "safe"
    interactive: bool = True


class Config(BaseModel):
    """Root bekas configuration model.

    Attributes:
        version: Config file format version.
        active_profile: Name of the active profile.
        profiles: Dictionary of named profiles.
        exclude: Global list of path patterns to exclude.
    """

    version: str = "1"
    active_profile: str = "default"
    profiles: dict[str, Profile] = {}
    exclude: list[str] = []


def _config_dir() -> Path:
    """Return the directory where bekas stores its configuration."""
    return Path(user_config_dir("bekas", appauthor=False))


def _data_dir() -> Path:
    """Return the directory where bekas stores its runtime data."""
    return Path(user_data_dir("bekas", appauthor=False))


def config_path() -> Path:
    """Return the path to the bekas configuration file."""
    return _config_dir() / "config.yaml"


def data_dir() -> Path:
    """Ensure and return the bekas data directory."""
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def quarantine_dir() -> Path:
    """Ensure and return the quarantine directory."""
    d = data_dir() / "quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runs_db_path() -> Path:
    """Return the path to the SQLite runs database."""
    return data_dir() / "runs.db"


def audit_log_path() -> Path:
    """Return the path to the audit log file."""
    return data_dir() / "audit.log"


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


def ensure_config() -> Path:
    """Create the default configuration file if it does not exist.

    Returns:
        Path to the configuration file.
    """
    cfg = config_path()
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(DEFAULT_CONFIG)
    return cfg


def load_config() -> Config:
    """Load and validate the bekas configuration file.

    Returns:
        Validated Config model.

    Raises:
        ValidationError: If the config file does not match the schema.
    """
    cfg = ensure_config()
    with cfg.open() as f:
        raw = yaml.safe_load(f) or {}
    return Config.model_validate(raw)


def active_profile(cfg: Config | None = None) -> Profile:
    """Return the active profile from config.

    Args:
        cfg: Optional pre-loaded Config. If None, loads from disk.

    Returns:
        The active Profile model.
    """
    if cfg is None:
        cfg = load_config()
    return cfg.profiles.get(cfg.active_profile, Profile())


def profile_for(profile_name: str | None = None) -> Profile:
    """Retrieve a specific or the active profile.

    Args:
        profile_name: Optional explicit profile name.

    Returns:
        Selected Profile model.
    """
    cfg = load_config()
    if profile_name:
        return cfg.profiles.get(profile_name, Profile())
    return active_profile(cfg)


def is_plugin_enabled(patterns: list[str], plugin_name: str) -> bool:
    """Check whether a plugin name matches any wildcard pattern.

    Args:
        patterns: List of glob-style patterns.
        plugin_name: Plugin name to test.

    Returns:
        True if the plugin name matches at least one pattern.
    """
    import fnmatch

    for pat in patterns:
        if pat == "*":
            return True
        if fnmatch.fnmatch(plugin_name, pat):
            return True
    return False


def validate_config_file(path: Path | None = None) -> tuple[bool, str]:
    """Validate a configuration file and return a friendly message.

    Args:
        path: Path to config file. Defaults to config_path().

    Returns:
        Tuple of (is_valid, message).
    """
    target = path or config_path()
    if not target.exists():
        return False, f"Config file not found: {target}"
    try:
        with target.open() as f:
            raw = yaml.safe_load(f) or {}
        Config.model_validate(raw)
        return True, "Config OK."
    except ValidationError as exc:
        lines = [f"Config validation error in {target}:"]
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            lines.append(f"  [{loc}] {err.get('msg', 'unknown error')}")
        return False, "\n".join(lines)
    except Exception as exc:
        return False, f"Config load error: {exc}"


def resolved_config(profile_name: str | None = None) -> dict:
    """Return the effective config as a merged dictionary.

    Args:
        profile_name: Optional explicit profile name.

    Returns:
        Dictionary with active_profile and merged profile settings.
    """
    cfg = load_config()
    profile = profile_for(profile_name)
    return {
        "active_profile": cfg.active_profile,
        "profile": profile.model_dump(),
        "exclude": cfg.exclude,
    }
