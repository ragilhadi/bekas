"""Branch coverage tests for formatters.py — sort, md, edge cases."""

from __future__ import annotations

from bekas.formatters import (
    _human_size,
    _sort_and_limit,
    format_human,
    format_json,
    format_md,
)
from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, Plan, PluginReport, RunResult, SystemInfo


def test_sort_by_tier():
    """Sort by confidence tier: SAFE < REVIEW < MANUAL."""
    cands = [
        Candidate(id="m", category="x", size_bytes=1, path_or_handle="/m", confidence=Confidence.MANUAL, reason="r"),
        Candidate(id="s", category="x", size_bytes=1, path_or_handle="/s", confidence=Confidence.SAFE, reason="r"),
        Candidate(id="r", category="x", size_bytes=1, path_or_handle="/r", confidence=Confidence.REVIEW, reason="r"),
    ]
    result = _sort_and_limit(cands, sort_by="tier")
    assert [c.id for c in result] == ["s", "r", "m"]


def test_sort_by_age():
    """Sort by age puts None-mtime last."""
    cands = [
        Candidate(
            id="a", category="x", size_bytes=1, path_or_handle="/a", confidence=Confidence.SAFE, reason="r", mtime=100
        ),
        Candidate(
            id="b", category="x", size_bytes=1, path_or_handle="/b", confidence=Confidence.SAFE, reason="r", mtime=200
        ),
        Candidate(id="c", category="x", size_bytes=1, path_or_handle="/c", confidence=Confidence.SAFE, reason="r"),
    ]
    result = _sort_and_limit(cands, sort_by="age")
    # None mtime gets -1, so it sorts first (youngest)
    assert [c.id for c in result] == ["c", "a", "b"]


def test_sort_by_size_reverse():
    """Sort by size is descending."""
    cands = [
        Candidate(id="s", category="x", size_bytes=100, path_or_handle="/s", confidence=Confidence.SAFE, reason="r"),
        Candidate(id="l", category="x", size_bytes=500, path_or_handle="/l", confidence=Confidence.SAFE, reason="r"),
    ]
    result = _sort_and_limit(cands, sort_by="size")
    assert [c.id for c in result] == ["l", "s"]


def test_top_zero_returns_all():
    """top=0 should not truncate (edge case)."""
    cands = [
        Candidate(id="a", category="x", size_bytes=1, path_or_handle="/a", confidence=Confidence.SAFE, reason="r"),
        Candidate(id="b", category="x", size_bytes=1, path_or_handle="/b", confidence=Confidence.SAFE, reason="r"),
    ]
    result = _sort_and_limit(cands, top=0)
    assert len(result) == 2


def test_format_human_empty_plan():
    """format_human on an empty Plan."""
    plan = Plan(audit_id="a1", candidates=[])
    text = format_human(plan)
    assert "Plan preview:" in text
    assert "Total: 0.0 B" in text


def test_format_human_runresult():
    """format_human on a RunResult."""
    from bekas.models import RemovalResult

    c = Candidate(id="x", category="foo", size_bytes=1024, path_or_handle="/x", confidence=Confidence.SAFE, reason="r")
    rr = RunResult(
        run_id="run_01",
        audit_id="a1",
        per_candidate=[(c, RemovalResult(success=True, bytes_freed=1024))],
        total_bytes_freed=1024,
    )
    text = format_human(rr)
    assert "run_01" in text
    assert "1.0 KB" in text
    assert "OK" in text


def test_format_md_basic():
    """format_md produces expected markdown."""
    from datetime import UTC, datetime

    pr = PluginReport(
        name="foo",
        candidates_found=1,
        candidates=[
            Candidate(
                id="x", category="foo", size_bytes=1024, path_or_handle="/x", confidence=Confidence.SAFE, reason="r"
            ),
        ],
    )
    report = AuditReport(
        audit_id="a1",
        started_at=datetime.now(UTC),
        plugins=[pr],
        summary=AuditSummary(total_candidates=1, total_bytes=1024, by_confidence={"safe": {"count": 1, "bytes": 1024}}),
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=10**12),
        duration_ms=1000,
    )
    md = format_md(report)
    assert "# Bekas Audit Report" in md
    assert "foo" in md
    assert "1.0 KB" in md


def test_format_json_plan():
    """format_json on a Plan."""
    plan = Plan(
        audit_id="a1",
        candidates=[
            Candidate(
                id="x", category="foo", size_bytes=1024, path_or_handle="/x", confidence=Confidence.SAFE, reason="r"
            ),
        ],
    )
    data = format_json(plan)
    assert "x" in data


def test_human_size_pb():
    """Sizes larger than TB render as PB."""
    assert "PB" in _human_size(1024**6)


def test_format_candidates():
    """format_candidates renders individual candidates."""
    from bekas.formatters import format_candidates

    cands = [
        Candidate(
            id="x",
            category="foo",
            size_bytes=1024,
            path_or_handle="/x",
            confidence=Confidence.SAFE,
            reason="test reason",
        ),
    ]
    text = format_candidates(cands)
    assert "x" in text
    assert "safe" in text.lower()
    assert "test reason" in text
    assert "/x" in text
