"""Tests for the plugin SDK, completions, and CI mode (issue #23)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from bekas.cli import cli
from bekas.plugin import load_plugins
from bekas.pluginsdk import API_VERSION, check_api_compatibility, scaffold_plugin


def test_check_api_compatibility():
    assert check_api_compatibility(None) is None  # legacy plugins keep loading
    assert check_api_compatibility(API_VERSION) is None
    msg = check_api_compatibility(API_VERSION + 5)
    assert msg is not None and "v" in msg
    assert check_api_compatibility("banana") is not None


def test_check_api_compatible_string_matches():
    assert check_api_compatibility(str(API_VERSION)) is None


def test_scaffold_creates_runnable_package(tmp_path):
    pkg = scaffold_plugin("demo", tmp_path)
    assert (pkg / "bekas_plugin_demo" / "plugin.py").exists()
    assert (pkg / "pyproject.toml").exists()
    assert (pkg / "test_plugin.py").exists()
    plugin_src = (pkg / "bekas_plugin_demo" / "plugin.py").read_text()
    assert "__bekas_api_version__" in plugin_src
    assert "class DemoPlugin(Plugin)" in plugin_src


def test_scaffold_rejects_bad_names(tmp_path):
    import pytest

    from bekas.pluginsdk import scaffold_plugin as sp

    with pytest.raises(ValueError):
        sp("../evil", tmp_path)
    with pytest.raises(ValueError):
        sp("", tmp_path)


def test_plugins_new_command(runner: CliRunner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["plugins", "new", "myspace", "--target", "."])
    assert result.exit_code == 0, result.output
    assert "scaffolded" in result.output
    assert (Path("bekas-plugin-myspace") / "bekas_plugin_myspace" / "plugin.py").exists()


def test_load_plugins_refuses_wrong_api_version(monkeypatch):
    """A fake entry point targeting another API version lands in errors."""
    import sys
    import types

    import bekas.plugin as plugin_mod

    fake_mod = types.ModuleType("fake_plugins")
    monkeypatch.setattr(sys, "modules", {**sys.modules, "fake_plugins": fake_mod})
    # entry point metadata is read inside load_plugins; stub the whole function's metadata access
    real_load = plugin_mod.load_plugins

    class _EP:
        name = "fake.api"

        def load(self):
            return fake_mod

    fake_mod.__bekas_api_version__ = API_VERSION + 1

    import importlib.metadata as metadata

    def fake_eps(group):
        assert group == "bekas.plugins"
        return [_EP()]

    monkeypatch.setattr(metadata, "entry_points", fake_eps)
    plugins, errors = load_plugins()
    assert plugins == []
    assert len(errors) == 1
    assert errors[0].entry_point == "fake.api"
    assert "v" in errors[0].error
    _ = real_load
    # restore


def test_completion_scripts_print(runner: CliRunner):
    for shell in ("bash", "zsh", "fish"):
        result = runner.invoke(cli, ["completion", shell])
        assert result.exit_code == 0, result.output
        assert "bekas" in result.output
    result = runner.invoke(cli, ["completion", "bash"])
    assert "complete -F _bekas_completion" in result.output


def test_audit_exit_code_above(runner, tmp_path, monkeypatch):
    """Threshold exceeded → exit code 4 with message on stderr."""
    from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, PluginReport, SystemInfo

    def rep_fn(plugins, **kw):
        return AuditReport(
            audit_id="a",
            started_at="2026-01-01T00:00:00+00:00",
            duration_ms=1,
            system=SystemInfo(os="linux", arch="x64", free_disk_bytes=1),
            plugins=[
                PluginReport(
                    name="fake.test",
                    candidates_found=1,
                    candidates=[
                        Candidate(
                            id="big",
                            category="fake.test",
                            size_bytes=10 * 1024**3,
                            path_or_handle="/x/big",
                            confidence=Confidence.SAFE,
                            reason="r",
                        )
                    ],
                    error=None,
                )
            ],
            summary=AuditSummary(total_candidates=1, total_bytes=10 * 1024**3, by_confidence={}),
        )

    from click.testing import CliRunner as CR

    import bekas.cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_audit", rep_fn)
    r = CR().invoke(cli, ["audit", "--exit-code-above", "5GB"])
    assert r.exit_code == 4, r.output
    assert "exceeds threshold" in r.output

    r2 = CR().invoke(cli, ["audit", "--exit-code-above", "50GB"])
    assert r2.exit_code == 0, r2.output

    r_bad = CR().invoke(cli, ["audit", "--exit-code-above", "lots"])
    assert r_bad.exit_code != 0


def test_audit_notify_best_effort(runner, monkeypatch):
    """--notify never fails the command, even when no notifier exists."""
    from bekas.models import AuditReport, AuditSummary, PluginReport, SystemInfo

    def rep_fn(plugins, **kw):
        return AuditReport(
            audit_id="a",
            started_at="2026-01-01T00:00:00+00:00",
            duration_ms=1,
            system=SystemInfo(os="linux", arch="x64", free_disk_bytes=1),
            plugins=[PluginReport(name="fake.test", candidates_found=0, candidates=[], error=None)],
            summary=AuditSummary(total_candidates=0, total_bytes=0, by_confidence={}),
        )

    import bekas.cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_audit", rep_fn)
    monkeypatch.setattr(cli_mod, "_send_desktop_notification", lambda message: None)
    r = runner.invoke(cli, ["audit", "--notify"])
    assert r.exit_code == 0, r.output


def test_version_command(runner: CliRunner):
    from bekas import __version__

    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output
