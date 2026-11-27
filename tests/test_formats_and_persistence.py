"""Tests for persisted audits, filters, plan diff and output formats (issue #21)."""

from __future__ import annotations

import json
import time

import pytest

from bekas.clean import diff_plans, select_interactive
from bekas.cli import _load_audit_ref, _parse_filter_arg, cli
from bekas.database import get_saved_audit, list_saved_audits, save_audit
from bekas.formats import apply_filters, format_output, parse_age, parse_size
from bekas.models import (
    AuditReport,
    AuditSummary,
    Candidate,
    Confidence,
    Plan,
    PluginReport,
    SystemInfo,
)


def _cand(cid: str, size: int = 100, mtime: int | None = None, cat: str = "fake.test") -> Candidate:
    return Candidate(
        id=cid,
        category=cat,
        size_bytes=size,
        path_or_handle=f"/x/{cid}",
        confidence=Confidence.SAFE,
        reason="r",
        mtime=mtime,
    )


def _report(candidates: list[Candidate], audit_id: str = "aud_x", duration_ms: int = 5) -> AuditReport:
    return AuditReport(
        audit_id=audit_id,
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=duration_ms,
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=1),
        plugins=[PluginReport(name="fake.test", candidates_found=len(candidates), candidates=candidates, error=None)],
        summary=AuditSummary(
            total_candidates=len(candidates),
            total_bytes=sum(c.size_bytes for c in candidates),
            by_confidence={},
        ),
    )


# --- parsers ---------------------------------------------------------------


def test_parse_size_units():
    assert parse_size("500MB") == 500 * 1024**2
    assert parse_size("1.5GB") == int(1.5 * 1024**3)
    assert parse_size("2048") == 2048
    assert parse_size(" 5 gb ") == 5 * 1024**3


def test_parse_size_rejects_garbage():
    with pytest.raises(ValueError):
        parse_size("huge")


def test_parse_age_units():
    assert parse_age("90d") == 90 * 86400
    assert parse_age("2w") == 2 * 604800
    assert parse_age("1y") == 31557600
    assert parse_age("12h") == 43200


def test_parse_filter_arg_bad_input_is_usage_error():
    import click

    with pytest.raises(click.BadParameter):
        _parse_filter_arg("notasize", parse_size)


# --- filters ---------------------------------------------------------------


def test_apply_filters_min_size():
    c1, c2 = _cand("a", 10), _cand("b", 1000)
    assert apply_filters([c1, c2], min_size=100) == [c2]


def test_apply_filters_older_than_requires_mtime():
    now = time.time()
    old = _cand("old", mtime=int(now - 100 * 86400))
    young = _cand("young", mtime=int(now - 86400))
    no_mtime = _cand("nomtime")
    out = apply_filters([old, young, no_mtime], older_than=90 * 86400)
    assert out == [old]


def test_apply_filters_combined():
    now = time.time()
    both = _cand("both", size=6000, mtime=int(now - 100 * 86400))
    out = apply_filters([both], min_size=500 * 1024**2, older_than=90 * 86400, now_ts=now)
    # 6 KB is not >= 500MB → filtered out
    assert out == []


# --- saved audits ----------------------------------------------------------


def test_save_and_get_audit_roundtrip():
    report = _report([_cand("a")])
    save_audit(report, name="myscan")
    assert get_saved_audit("aud_x").audit_id == "aud_x"
    assert get_saved_audit("myscan").audit_id == "aud_x"
    assert get_saved_audit("last").audit_id == "aud_x"
    assert [r["name"] for r in list_saved_audits()] == ["myscan"]
    assert get_saved_audit("nope") is None


def test_load_audit_ref_missing_exits():
    with pytest.raises(SystemExit) as exc:
        _load_audit_ref("ghost")
    assert exc.value.code == 1


# --- plan diff -------------------------------------------------------------


def test_diff_plans_reports_added_removed_changed():
    common = _cand("a", 100)
    old = Plan(audit_id="x", candidates=[common, _cand("b", 200)])
    new = Plan(audit_id="y", candidates=[common, _cand("c", 300), _cand("a", 100)])
    _ = new
    # same id 'a' but different size → changed
    new2 = Plan(audit_id="y", candidates=[_cand("a", 150), _cand("c", 300)])
    delta = diff_plans(old, new2)
    assert [c.id for c in delta["added"]] == ["c"]
    assert [c.id for c in delta["removed"]] == ["b"]
    assert [c.id for c in delta["changed"]] == ["a"]


# --- interactive picker ----------------------------------------------------


def test_select_interactive_toggles_and_confirms(monkeypatch, capsys):
    c1, c2 = _cand("a"), _cand("b", 200)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")
    picked = select_interactive([c1, c2])
    assert picked == [c2]


def test_select_interactive_quit_returns_empty(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "q")
    assert select_interactive([_cand("a")]) == []


# --- CLI surface ------------------------------------------------------------


def _saved_report_with(candidates: list[Candidate], audit_id: str = "aud_cli") -> AuditReport:
    return _report(candidates, audit_id=audit_id, duration_ms=0)


def test_cli_audit_save_then_plan_from_audit_no_rescan(runner, monkeypatch):
    report = _saved_report_with([_cand("a"), _cand("b", 10**9, mtime=int(time.time() - 400 * 86400))])
    calls: list[list] = []

    def fake_run_audit(plugins, **kwargs):
        calls.append(list(plugins))
        return report

    import bekas.cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_audit", fake_run_audit)
    r1 = runner.invoke(cli, ["audit", "--save", "myscan"])
    assert r1.exit_code == 0, r1.output
    assert "Audit saved" in r1.output

    r2 = runner.invoke(cli, ["plan", "--from-audit", "last", "--json"])
    assert r2.exit_code == 0, r2.output
    assert "no rescan" in r2.output
    json_blob = r2.output[r2.output.index("{") :]
    data = json.loads(json_blob)
    assert calls, "audit --save should have run once"
    # plan --from-audit must NOT trigger another run_audit
    assert len(calls) == 1
    assert {c["id"] for c in data["candidates"]} >= {"a"}


def test_cli_audit_formats(runner, monkeypatch):
    report = _saved_report_with([_cand("a", 123)])
    import bekas.cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_audit", lambda plugins, **kw: report)
    r_csv = runner.invoke(cli, ["audit", "--format", "csv"])
    assert r_csv.exit_code == 0, r_csv.output
    assert "id,category,size_bytes" in r_csv.output
    assert "a,fake.test,123" in r_csv.output

    r_nd = runner.invoke(cli, ["audit", "--format", "ndjson"])
    assert r_nd.exit_code == 0
    data_lines = [ln for ln in r_nd.output.strip().splitlines() if ln.startswith("{")]
    assert json.loads(data_lines[0])["id"] == "a"


def test_cli_audit_filters(runner, monkeypatch):
    report = _saved_report_with([_cand("small", 10), _cand("big", 10**9)])
    import bekas.cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_audit", lambda plugins, **kw: report)
    r = runner.invoke(cli, ["--json", "audit", "--min-size", "1MB"])
    assert r.exit_code == 0, r.output
    json_blob = r.output[r.output.index("{") :]
    data = json.loads(json_blob)
    ids = {c["id"] for pr in data["plugins"] for c in pr["candidates"]}
    assert ids == {"big"}

    r_bad = runner.invoke(cli, ["audit", "--min-size", "nope"])
    assert r_bad.exit_code != 0


def test_cli_plan_diff(runner, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    old = Plan(
        audit_id="old",
        candidates=[_cand("a", 100), _cand("b", 200)],
    )
    plan_file = tmp_path / "old_plan.json"
    plan_file.write_text(json.dumps(old.model_dump(mode="json")))

    new_report = _saved_report_with([_cand("a", 150), _cand("c", 300)], audit_id="aud_new")
    import bekas.cli as cli_mod

    monkeypatch.setattr(cli_mod, "get_saved_audit", lambda ref, db_path=None: new_report)
    r = runner.invoke(cli, ["plan", "--from-audit", "aud_new", "--diff", str(plan_file)])
    assert r.exit_code == 0, r.output
    assert "Added: 1" in r.output and "c" in r.output
    assert "Removed: 1" in r.output and "b" in r.output
    assert "Changed: 1" in r.output and "100.0 B → 150.0 B" in r.output


def test_cli_clean_interactive_picks(runner, monkeypatch, tmp_path):
    """--interactive applies only toggled candidates, then the typed gate."""
    a = tmp_path / "a.txt"
    a.write_text("a")
    report = _saved_report_with(
        [
            _cand(
                "pick:a",
                1,
                cat="fake.test",
            )
        ]
    )
    report.plugins[0].candidates[0] = report.plugins[0].candidates[0].model_copy(update={"path_or_handle": str(a)})
    import bekas.cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_audit", lambda plugins, **kw: report)
    monkeypatch.setattr(cli_mod, "_stdin_is_tty", lambda: True)
    answers = iter(["1"])  # toggle item 1 off → nothing selected
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    r = runner.invoke(cli, ["clean", "--interactive"])
    assert r.exit_code == 0, r.output
    assert "Nothing selected." in r.output
    assert a.exists()


def test_select_interactive_shows_summary_before_gate(monkeypatch, capsys):
    c1, c2 = _cand("a", 1), _cand("b", 2)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")
    picked = select_interactive([c1, c2])
    out = capsys.readouterr().out
    assert "Selected: 1 item(s)" in out
    assert picked == [c2]


# --- formats module ---------------------------------------------------------


def test_format_output_csv_plan():
    plan = Plan(audit_id="x", candidates=[_cand("z", 5)])
    out = format_output(plan, "csv", "")
    assert out.startswith("id,category,size_bytes")
    assert "z,fake.test,5" in out


def test_format_output_table_returns_fallback():
    assert format_output(Plan(audit_id="x"), "table", "FALLBACK") == "FALLBACK"


def test_format_output_unknown_raises():
    with pytest.raises(ValueError):
        format_output(Plan(audit_id="x"), "xml", "")
