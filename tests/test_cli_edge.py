"""CLI edge case tests for uncovered branches."""

import json
from unittest.mock import patch

from bekas.cli import cli


def test_cli_clean_noninteractive_without_yes_all_fails(runner, monkeypatch):
    result = runner.invoke(cli, ["clean", "--apply", "--non-interactive"])
    assert result.exit_code == 2
    assert "--non-interactive requires" in result.output.lower()


def test_cli_tui_import_failure(runner, monkeypatch):
    """Simulate TUI import failure."""
    with patch("bekas.tui.TuiApp", side_effect=ImportError("no textual")):
        result = runner.invoke(cli, ["tui"])
        assert result.exit_code == 1
        assert "TUI not available" in result.output


def test_cli_config_validate_bad_config(runner, monkeypatch, tmp_path):
    """Make load_config return a non-dict so validation fails."""
    # Patch the load_config reference inside cli.py's namespace
    with patch("bekas.cli.load_config", return_value=[]):
        result = runner.invoke(cli, ["config", "validate"])
        assert result.exit_code == 1
        assert "not a valid dictionary" in result.output.lower()


def test_cli_clean_with_plan_file_tampered(runner, monkeypatch, tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_data = {
        "audit_id": "ad_test",
        "created_at": "2024-01-01T00:00:00Z",
        "candidates": [],
        "_signature": "bad-signature",
    }
    plan_file.write_text(json.dumps(plan_data))
    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--plan-file", str(plan_file)])
    assert result.exit_code == 1
    assert "signature is invalid" in result.output.lower()
