"""Tests for CLI welcome / default flow."""

import sys

from bekas.cli import cli


def test_cli_welcome_with_tty_no(runner, monkeypatch):
    # Simulate tty but user says no
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    result = runner.invoke(cli, [], input="n\n")
    assert result.exit_code == 0
    assert "Next steps" in result.output or "bekas audit" in result.output


def test_cli_welcome_non_tty(runner, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    assert "Run 'bekas audit'" in result.output
