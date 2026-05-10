"""Tests for config module."""

from bekas.config import Config, Profile, active_profile, ensure_config, is_plugin_enabled, load_config


def test_ensure_config_creates_file():
    path = ensure_config()
    assert path.exists()


def test_load_config():
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert "default" in cfg.profiles


def test_active_profile():
    cfg = load_config()
    profile = active_profile(cfg)
    assert isinstance(profile, Profile)
    assert profile.safety_threshold == "safe"
    assert profile.interactive is True
    assert profile.quarantine_enabled is True
    assert profile.plugin_timeout_seconds == 60


def test_is_plugin_enabled():
    assert is_plugin_enabled(["*"], "docker.images")
    assert is_plugin_enabled(["docker.*"], "docker.images")
    assert not is_plugin_enabled(["python.*"], "docker.images")
