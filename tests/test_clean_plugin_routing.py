"""Regression tests: candidates must be routed to the plugin that owns their category."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from bekas.clean import CleanAborted, _find_plugin, apply_plan, typed_confirmation_gate
from bekas.models import Candidate, Confidence, Context, Plan, RemovalResult
from bekas.plugin import Plugin, discover_plugins

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "src" / "bekas" / "plugins"


def _cand(category: str, handle: str, cid: str = "x:1") -> Candidate:
    return Candidate(
        id=cid, category=category, size_bytes=10, path_or_handle=handle, confidence=Confidence.SAFE, reason="r"
    )


def _emitted_categories() -> set[str]:
    cats: set[str] = set()
    for f in PLUGINS_DIR.glob("*.py"):
        cats.update(re.findall(r'category="([^"]+)"', f.read_text()))
    return cats


def test_every_builtin_category_resolves_to_a_plugin():
    plugins = discover_plugins()
    unresolved = sorted(c for c in _emitted_categories() if _find_plugin(plugins, c) is None)
    assert unresolved == []


class _Recorder(Plugin):
    name = "docker.images"
    categories = ("docker.image.dangling",)
    description = "recorder"

    def __init__(self) -> None:
        self.removed: list[str] = []

    def discover(self, ctx):
        yield from []

    def remove(self, candidate, ctx):
        self.removed.append(candidate.id)
        return RemovalResult(success=True, bytes_freed=candidate.size_bytes, log="deleted")


def test_apply_plan_routes_subcategory_to_owning_plugin():
    plugin = _Recorder()
    plan = Plan(audit_id="a", candidates=[_cand("docker.image.dangling", "docker://abc")])
    result = apply_plan(plan, [plugin], Context(dry_run=False), yes_all=True)
    assert plugin.removed == ["x:1"]
    assert result.total_bytes_freed == 10


def test_opaque_handle_without_plugin_is_never_treated_as_path():
    plan = Plan(audit_id="a", candidates=[_cand("unknown.thing", "docker://abc")])
    with patch("bekas.clean._generic_remove") as generic:
        result = apply_plan(plan, [], Context(dry_run=False), yes_all=True)
    generic.assert_not_called()
    (_, res) = result.per_candidate[0]
    assert res.success is False
    assert "No plugin handles category" in res.log


def test_declined_confirmation_does_not_purge_quarantine():
    plan = Plan(audit_id="a", candidates=[_cand("fake", "/tmp/fake")])
    with (
        patch("bekas.clean.typed_confirmation_gate", return_value=False),
        patch("bekas.clean.purge_old_quarantine") as purge,
        pytest.raises(CleanAborted),
    ):
        apply_plan(plan, [], Context(dry_run=False))
    purge.assert_not_called()


def test_purge_runs_after_confirmation_and_failure_does_not_abort():
    plugin = _Recorder()
    plan = Plan(audit_id="a", candidates=[_cand("docker.image.dangling", "docker://abc")])
    with patch("bekas.clean.purge_old_quarantine", side_effect=ValueError("bad row")) as purge:
        result = apply_plan(plan, [plugin], Context(dry_run=False), yes_all=True)
    purge.assert_called_once()
    assert plugin.removed == ["x:1"]
    assert result.total_bytes_freed == 10


def test_dry_run_does_not_purge_quarantine():
    plan = Plan(audit_id="a", candidates=[_cand("docker.image.dangling", "docker://abc")])
    with patch("bekas.clean.purge_old_quarantine") as purge:
        apply_plan(plan, [_Recorder()], Context(dry_run=True))
    purge.assert_not_called()


@pytest.mark.parametrize("risky", [False, True])
def test_closed_stdin_aborts_gate(monkeypatch, risky):
    conf = Confidence.MANUAL if risky else Confidence.SAFE
    c = Candidate(id="x", category="c", size_bytes=1, path_or_handle="/x", confidence=conf, reason="")
    plan = Plan(audit_id="a", candidates=[c])

    def _eof(*_a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert typed_confirmation_gate(plan, quarantine_enabled=True) is False


def test_cli_clean_exits_nonzero_when_aborted(monkeypatch):
    from click.testing import CliRunner

    from bekas.cli import cli

    plan = Plan(audit_id="a", candidates=[_cand("fake", "/tmp/fake")])
    monkeypatch.setattr("bekas.cli.run_audit", lambda *a, **k: None)
    monkeypatch.setattr("bekas.cli._audit_to_plan", lambda *a, **k: plan)
    monkeypatch.setattr("bekas.cli._get_plugins", lambda: [])
    monkeypatch.setattr("bekas.clean.typed_confirmation_gate", lambda *a, **k: False)
    monkeypatch.setattr("bekas.locking.ProcessLock.acquire", lambda self: None)
    monkeypatch.setattr("bekas.locking.ProcessLock.release", lambda self: None)
    result = CliRunner().invoke(cli, ["clean", "--apply"])
    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert "Traceback" not in result.output
