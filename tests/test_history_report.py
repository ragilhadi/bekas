"""Tests for `bekas history <run> --report` markdown export (issue #22)."""

from __future__ import annotations

import json
from pathlib import Path

from bekas.cli import cli
from bekas.database import get_run, log_run
from bekas.models import Candidate, Confidence, RemovalResult, RunResult
from bekas.quarantine import move_to_quarantine


def _run_with_one_quarantined(tmp_path: Path, run_id: str = "run_rep") -> Path:
    src = tmp_path / "gone.txt"
    src.write_text("data")
    dest = move_to_quarantine(run_id, src, "fake.test", 4, {})
    cand = Candidate(
        id="c:rep",
        category="fake.test",
        size_bytes=4,
        path_or_handle=str(src),
        confidence=Confidence.SAFE,
        reason="Old download",
    )
    log_run(
        RunResult(
            run_id=run_id,
            audit_id="audit-1",
            per_candidate=[(cand, RemovalResult(success=True, bytes_freed=4, undo_token=str(dest), log="quarantined"))],
            total_bytes_freed=4,
        )
    )
    return src


def test_history_report_writes_markdown(runner, tmp_path):
    _run_with_one_quarantined(tmp_path)
    out = tmp_path / "report.md"
    result = runner.invoke(cli, ["history", "run_rep", "--report", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text()
    assert text.startswith("# bekas run run_rep")
    assert "gone.txt" in text
    assert "fake.test" in text
    assert "Old download" in text
    assert "| Candidate | Category | Size | State | Reason |" in text
    assert "restorable" in text


def test_history_report_without_run_id_errors(runner, tmp_path):
    out = tmp_path / "report.md"
    result = runner.invoke(cli, ["history", "--report", str(out)])
    assert result.exit_code == 2


def test_history_report_includes_summary(runner, tmp_path):
    _run_with_one_quarantined(tmp_path)
    out = tmp_path / "report.md"
    runner.invoke(cli, ["history", "run_rep", "--report", str(out)])
    text = out.read_text()
    assert "## Summary" in text
    assert "Candidates acted on: 1" in text
    assert "Still restorable: 1" in text


def test_report_reflects_undo_state(runner, tmp_path):
    src = _run_with_one_quarantined(tmp_path)

    class _Ctx:
        dry_run = False
        config: dict = {}

        def run_command(self, cmd, cwd=None):
            raise AssertionError

        def which(self, name):
            return None

    results = json.loads(get_run("run_rep")["results_json"])
    from bekas.quarantine import undo_run_results

    undo_run_results("run_rep", results)
    out = tmp_path / "report2.md"
    runner.invoke(cli, ["history", "run_rep", "--report", str(out)])
    text = out.read_text()
    assert "restored" in text
    assert src.exists()
