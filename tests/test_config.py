"""Tests for config module."""

from bekas.config import active_profile, ensure_config, is_plugin_enabled, load_config


def test_ensure_config_creates_file():
    path = ensure_config()
    assert path.exists()


def test_load_config():
    cfg = load_config()
    assert "profiles" in cfg
    assert "default" in cfg["profiles"]


def test_active_profile():
    cfg = load_config()
    profile = active_profile(cfg)
    assert profile["safety_threshold"] == "safe"


def test_is_plugin_enabled():
    assert is_plugin_enabled(["*"], "docker.images")
    assert is_plugin_enabled(["docker.*"], "docker.images")
    assert not is_plugin_enabled(["python.*"], "docker.images")
