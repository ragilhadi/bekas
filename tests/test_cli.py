"""CLI smoke tests using click.testing.CliRunner."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from bekas.cli import cli
from bekas.plugin import Capabilities, Plugin


@pytest.fixture(autouse=True)
def _patch_data_dir(monkeypatch):
    """Redirect data and config dirs into a temporary path for CLI tests."""
    import tempfile
    from pathlib import Path

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

    # Patch permissive profile at the source module level and in all consumers
    _permissive = cfg.Profile(interactive=False, quarantine_enabled=False, enabled_plugins=["*"])
    _permissive_cfg = cfg.Config(active_profile="default", profiles={"default": _permissive})
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


class _FakePlugin(Plugin):
    name = "fake.test"
    description = "Fake plugin for testing."
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx):
        return True

    def discover(self, ctx):
        from bekas.models import Candidate, Confidence

        yield Candidate(
            id="fake:1",
            category="fake.test",
            size_bytes=1000,
            path_or_handle="/tmp/fake_thing",
            confidence=Confidence.SAFE,
            reason="Fake candidate for testing.",
        )

    def remove(self, candidate, ctx):
        from bekas.models import RemovalResult

        return RemovalResult(success=True, bytes_freed=candidate.size_bytes, log="deleted")


def test_cli_audit_output(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    result = runner.invoke(cli, ["audit", "--plugin", "fake.test"])
    assert result.exit_code == 0
    assert "Audit complete" in result.output


def test_cli_audit_json(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    result = runner.invoke(cli, ["--json", "audit", "--plugin", "fake.test"])
    assert result.exit_code == 0
    # JSON may span multiple lines; extract from first '{' to end
    start = result.output.index("{")
    data = json.loads(result.output[start:])
    assert data.get("audit_id", "").startswith("ad_")


def test_cli_plan_output(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    result = runner.invoke(cli, ["plan"])
    assert result.exit_code == 0
    assert "Plan preview" in result.output


def test_cli_plan_save_and_clean_signed(runner, monkeypatch, tmp_path):
    """Save a signed plan via --save and then clean it with --plan-file."""
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    plan_file = tmp_path / "plan.json"
    result = runner.invoke(cli, ["plan", "--save", str(plan_file)])
    assert result.exit_code == 0
    plan_data = json.loads(plan_file.read_text())
    assert "_signature" in plan_data

    result = runner.invoke(cli, ["clean", "--apply", "--yes-all", "--plan-file", str(plan_file)])
    assert result.exit_code == 0
    assert "Total freed" in result.output


def test_cli_plan_save_tampered_plan(runner, monkeypatch, tmp_path):
    """Tampered plan file should be rejected."""
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    plan_file = tmp_path / "plan.json"
    result = runner.invoke(cli, ["plan", "--save", str(plan_file)])
    assert result.exit_code == 0

    plan_data = json.loads(plan_file.read_text())
    plan_data["candidates"].append(
        {
            "id": "bad:1",
            "category": "bad.malicious",
            "size_bytes": 999999,
            "path_or_handle": "/etc/passwd",
            "confidence": "safe",
            "reason": "tampered",
        }
    )
    plan_file.write_text(json.dumps(plan_data))

    result = runner.invoke(cli, ["clean", "--apply", "--plan-file", str(plan_file)])
    assert result.exit_code == 1
    assert "signature is invalid" in result.output.lower()


def test_cli_inspect(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    result = runner.invoke(cli, ["inspect", "fake:1"])
    assert result.exit_code == 0
    assert "Candidate: fake:1" in result.output


def test_cli_config(runner, monkeypatch):
    """Ensure config command prints configuration."""
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    result = runner.invoke(cli, ["config"])
    assert result.exit_code == 0
    assert "Config file" in result.output


def test_cli_plugins_list(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    result = runner.invoke(cli, ["plugins", "list"])
    assert result.exit_code == 0
    assert "fake.test" in result.output


def test_cli_history_no_runs(runner, monkeypatch):
    result = runner.invoke(cli, ["history"])
    assert result.exit_code == 0
    assert "No history yet" in result.output or "Run ID" in result.output


def test_cli_dry_run_defaults(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_FakePlugin()])
    result = runner.invoke(cli, ["clean"])
    assert result.exit_code == 0
    assert "Dry run mode" in result.output


def test_cli_quarantine_list_empty(runner, monkeypatch):
    result = runner.invoke(cli, ["quarantine", "list"])
    assert result.exit_code == 0
    assert "Quarantine is empty" in result.output


def test_cli_quarantine_purge(runner, monkeypatch):
    result = runner.invoke(cli, ["quarantine", "purge"])
    assert result.exit_code == 0
    assert "Purged" in result.output


def test_cli_undo_no_runs(runner, monkeypatch):
    result = runner.invoke(cli, ["undo"])
    assert result.exit_code == 0
    assert "No runs to undo" in result.output or "run_" in result.output
