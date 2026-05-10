"""Tests for CLI config subcommands."""

from bekas.cli import cli


def test_cli_config_validate(runner, monkeypatch):
    """Ensure config validate passes with the permissive test config."""
    result = runner.invoke(cli, ["config", "validate"])
    assert result.exit_code == 0
    assert "Config OK" in result.output or "config ok" in result.output.lower()


def test_cli_config_show_resolved(runner, monkeypatch):
    result = runner.invoke(cli, ["config", "show", "--resolved"])
    assert result.exit_code == 0
    assert "profile" in result.output.lower() or "active_profile" in result.output.lower()
