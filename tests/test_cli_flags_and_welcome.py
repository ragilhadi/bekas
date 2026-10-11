"""Regression tests: tier flags, --top semantics and the first-run welcome."""

from __future__ import annotations

import pytest

from bekas.cli import _audit_to_plan, cli
from bekas.formatters import format_human
from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, Plan, PluginReport, SystemInfo
from bekas.plugin import Plugin


def _cand(cid: str, conf: Confidence = Confidence.SAFE, size: int = 10, category: str = "fake.test") -> Candidate:
    return Candidate(
        id=cid, category=category, size_bytes=size, path_or_handle=f"/nonexistent/{cid}", confidence=conf, reason="r"
    )


class _Mixed(Plugin):
    name = "fake.test"
    description = "d"

    def discover(self, ctx):
        yield _cand("safe:1")
        yield _cand("review:1", Confidence.REVIEW)


@pytest.fixture(autouse=True)
def _plugins(monkeypatch):
    monkeypatch.setattr("bekas.cli.discover_plugins", lambda: [_Mixed()])


# ── tier flags ─────────────────────────────────────────────────


def test_default_plan_contains_only_safe_items():
    report = AuditReport(
        audit_id="a",
        started_at=__import__("datetime").datetime.now(),
        duration_ms=0,
        system=SystemInfo(os="linux", arch="x", free_disk_bytes=0),
        plugins=[PluginReport(name="p", candidates_found=2, candidates=[_cand("s"), _cand("r", Confidence.REVIEW)])],
        summary=AuditSummary(),
    )
    assert [c.id for c in _audit_to_plan(report).candidates] == ["s"]
    assert [c.id for c in _audit_to_plan(report, include_review=True).candidates] == ["s", "r"]
    # safe_only wins even if both slip through
    assert [c.id for c in _audit_to_plan(report, safe_only=True, include_review=True).candidates] == ["s"]


@pytest.mark.parametrize(
    "args",
    [
        ["plan", "--safe-only", "--include-review"],
        ["clean", "--safe-only", "--review"],
    ],
)
def test_contradictory_tier_flags_are_rejected(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 2
    assert "--safe-only cannot be combined" in result.output


def test_review_flag_adds_review_items_to_the_plan(runner):
    without = runner.invoke(cli, ["plan"])
    with_review = runner.invoke(cli, ["plan", "--include-review"])
    assert "review:1" not in without.output
    assert "review:1" in with_review.output


def test_help_text_matches_behaviour(runner):
    clean_help = " ".join(runner.invoke(cli, ["clean", "--help"]).output.split())
    assert "interactively" not in clean_help
    assert "typed confirmation" in clean_help


# ── --top ──────────────────────────────────────────────────────


def _report(n_a: int, n_b: int) -> AuditReport:
    return AuditReport(
        audit_id="a",
        started_at=__import__("datetime").datetime.now(),
        duration_ms=0,
        system=SystemInfo(os="linux", arch="x", free_disk_bytes=0),
        plugins=[
            PluginReport(
                name="a.one", candidates_found=n_a, candidates=[_cand(f"a{i}", size=i + 1) for i in range(n_a)]
            ),
            PluginReport(
                name="b.two", candidates_found=n_b, candidates=[_cand(f"b{i}", size=i + 1) for i in range(n_b)]
            ),
        ],
        summary=AuditSummary(total_candidates=n_a + n_b),
    )


def test_audit_top_limits_each_plugin_and_says_so():
    text = format_human(_report(5, 2), sort_by="size", top=2)
    assert "a4" in text and "a3" in text
    assert "a0" not in text
    assert "... and 3 more (omitted by --top)" in text
    assert "b1" in text and "b0" in text  # second plugin is not squeezed out
    assert " 5 found" in text  # per-plugin count still covers everything


def test_plan_top_limits_each_category_but_not_totals():
    plan = Plan(
        audit_id="a",
        candidates=[_cand(f"x{i}", size=i + 1, category="cat.x") for i in range(4)]
        + [_cand(f"y{i}", size=100, category="cat.y") for i in range(2)],
    )
    text = format_human(plan, sort_by="size", top=1)
    lines = text.splitlines()
    x_line = next(line for line in lines if line.strip().startswith("cat.x"))
    y_line = next(line for line in lines if line.strip().startswith("cat.y"))
    assert " 4 items" in x_line  # counts cover the whole category, not just what is listed
    assert " 2 items" in y_line
    assert "... and 3 more (omitted by --top)" in text
    assert "... and 1 more (omitted by --top)" in text
    assert text.endswith(format_human(plan).splitlines()[-1])  # same total with and without --top


def test_no_top_lists_everything():
    assert "omitted by --top" not in format_human(_report(5, 2))


@pytest.mark.parametrize("value", ["0", "-3"])
def test_top_must_be_positive(runner, value):
    for cmd in ("audit", "plan"):
        result = runner.invoke(cli, [cmd, "--top", value])
        assert result.exit_code == 2


# ── welcome ────────────────────────────────────────────────────


def test_first_run_shows_the_welcome(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli._stdin_is_tty", lambda: False)
    result = runner.invoke(cli, [])
    assert "Welcome to bekas" in result.output
    assert "Run 'bekas audit'" in result.output


def test_welcome_is_not_marked_when_output_was_piped(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli._stdin_is_tty", lambda: False)
    runner.invoke(cli, [])
    again = runner.invoke(cli, [])
    assert "Welcome to bekas" in again.output  # a later interactive run still gets it


def test_welcome_is_shown_once_after_an_interactive_run(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli._stdin_is_tty", lambda: True)
    first = runner.invoke(cli, [], input="n\n")
    assert "You haven't run an audit yet" in first.output
    second = runner.invoke(cli, [])
    assert "Welcome to bekas" not in second.output
    assert "haven't run an audit" not in second.output
    assert "Usage:" in second.output


def test_running_any_command_ends_the_first_run(runner, monkeypatch):
    assert runner.invoke(cli, ["audit"]).exit_code == 0
    monkeypatch.setattr("bekas.cli._stdin_is_tty", lambda: True)
    result = runner.invoke(cli, [])
    assert "haven't run an audit" not in result.output
    assert "Usage:" in result.output


def test_recorded_clean_run_also_ends_the_first_run(runner, monkeypatch):
    monkeypatch.setattr("bekas.cli.list_runs", lambda: [{"run_id": "run_1"}])
    result = runner.invoke(cli, [])
    assert "Welcome to bekas" not in result.output
    assert "Usage:" in result.output
