"""Tests for selective undo (issue #22): --list, --item, plugin-based undo."""

from __future__ import annotations

import json
from pathlib import Path

from bekas.cli import cli
from bekas.database import get_run, log_run
from bekas.models import Candidate, Confidence, RemovalResult, RunResult
from bekas.quarantine import move_to_quarantine, undo_item_ids, undo_run_item, undo_run_results


def _quarantined_run(tmp_path: Path, run_id: str = "run_sel") -> tuple[Path, Path, str]:
    a = tmp_path / "alpha.txt"
    b = tmp_path / "beta.txt"
    a.write_text("a")
    b.write_text("b")
    dest_a = move_to_quarantine(run_id, a, "fake.test", 1, {})
    dest_b = move_to_quarantine(run_id, b, "fake.test", 1, {})
    cands = [
        Candidate(
            id="c:a", category="fake.test", size_bytes=1, path_or_handle=str(a), confidence=Confidence.SAFE, reason=""
        ),
        Candidate(
            id="c:b", category="fake.test", size_bytes=1, path_or_handle=str(b), confidence=Confidence.SAFE, reason=""
        ),
    ]
    log_run(
        RunResult(
            run_id=run_id,
            audit_id="audit",
            per_candidate=[
                (cands[0], RemovalResult(success=True, bytes_freed=1, undo_token=str(dest_a), log="quarantined")),
                (cands[1], RemovalResult(success=True, bytes_freed=1, undo_token=str(dest_b), log="quarantined")),
            ],
            total_bytes_freed=2,
        )
    )
    return a, b, run_id


def test_undo_item_ids_lists_states(tmp_path):
    _, _, run_id = _quarantined_run(tmp_path)
    rows = undo_item_ids(run_id)
    assert [r["candidate_id"] for r in rows] == ["c:a", "c:b"]
    assert all(r["state"] == "restorable" for r in rows)


def test_undo_item_ids_marks_restored(tmp_path):
    _, _, run_id = _quarantined_run(tmp_path)
    results = json.loads(get_run(run_id)["results_json"])
    undo_run_results(run_id, results)
    rows = undo_item_ids(run_id)
    assert all(r["state"] == "restored" for r in rows)


def test_undo_run_item_restores_single_file(tmp_path):
    a, b, run_id = _quarantined_run(tmp_path)
    results = json.loads(get_run(run_id)["results_json"])
    # run plan candidates keyed by id
    plan_cands = [
        Candidate(
            id="c:a", category="fake.test", size_bytes=1, path_or_handle=str(a), confidence=Confidence.SAFE, reason=""
        ),
        Candidate(
            id="c:b", category="fake.test", size_bytes=1, path_or_handle=str(b), confidence=Confidence.SAFE, reason=""
        ),
    ]
    outcome = undo_run_item(run_id, results, "c:a", candidates=plan_cands)
    assert outcome.restored == 1
    assert a.exists()
    assert not b.exists()  # untouched


def test_undo_run_item_unknown_id_raises(tmp_path):
    _, _, run_id = _quarantined_run(tmp_path)
    results = json.loads(get_run(run_id)["results_json"])
    try:
        undo_run_item(run_id, results, "nope")
        raised = False
    except KeyError:
        raised = True
    assert raised


def test_undo_item_is_idempotent(tmp_path):
    a, _, run_id = _quarantined_run(tmp_path)
    results = json.loads(get_run(run_id)["results_json"])
    undo_run_item(run_id, results, "c:a")
    # second attempt of the same item reports failure (destination exists),
    # and does not duplicate the restore.
    outcome2 = undo_run_item(run_id, results, "c:a")
    content = a.read_text()
    assert content == "a"
    assert outcome2.restored == 0


def test_cli_undo_list(runner, tmp_path):
    _, _, run_id = _quarantined_run(tmp_path)
    result = runner.invoke(cli, ["undo", run_id, "--list"])
    assert result.exit_code == 0
    assert "c:a" in result.output
    assert "restorable" in result.output


def test_cli_undo_item_restores_one(runner, tmp_path):
    a, b, run_id = _quarantined_run(tmp_path)
    result = runner.invoke(cli, ["undo", run_id, "--item", "c:a"])
    assert result.exit_code == 0, result.output
    assert a.exists()
    assert not b.exists()


def test_cli_undo_item_unknown_exits_two(runner, tmp_path):
    _, _, run_id = _quarantined_run(tmp_path)
    result = runner.invoke(cli, ["undo", run_id, "--item", "ghost"])
    assert result.exit_code == 2
    assert "--list" in result.output
