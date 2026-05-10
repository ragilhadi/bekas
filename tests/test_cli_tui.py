"""Tests for CLI tui command."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from bekas.cli import cli


def test_cli_tui_not_available(runner, monkeypatch):
    """When TUI imports fail, the command exits gracefully."""
    with patch.dict("sys.modules", {"bekas.tui": None}):
        # Force an import error when cli tries to import TuiApp
        result = runner.invoke(cli, ["tui"])
        assert result.exit_code == 1
        assert "TUI not available" in result.output


@pytest.fixture
def runner():
    return CliRunner()
