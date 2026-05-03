"""Tests for output formatters."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from bekas.formatters import (
    _human_size,
    _sort_and_limit,
    _totals_header,
    format_candidates,
    format_human,
    format_json,
    format_md,
)
from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, Plan, RunResult, SystemInfo


def test_human_size():
    assert _human_size(0) == "0.0 B"
    assert _human_size(1024) == "1.0 KB"
    assert _human_size(1024 * 1024) == "1.0 MB"
    assert _human_size(1024**5) == "1.0 PB"


def test_format_human_audit_report():
    from datetime import datetime

    c = Candidate(
        id="a", category="docker.image", size_bytes=1000, path_or_handle="x", confidence=Confidence.SAFE, reason="r"
    )
    report = AuditReport(
        audit_id="ad1",
        started_at=datetime.now(UTC),
        duration_ms=1000,
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=1024),
        plugins=[
            {
                "name": "docker.images",
                "candidates_found": 1,
                "candidates": [c],
            }
        ],  # type: ignore[arg-type]
        summary=AuditSummary(total_candidates=1, total_bytes=1000, by_confidence={"safe": {"count": 1, "bytes": 1000}}),
    )
    text = format_human(report)
    assert "Audit complete" in text
    assert "docker.images" in text
    assert "Total reclaimable" in text


def test_totals_header_renders():
    """_totals_header produces headline block."""
    report = AuditReport(
        audit_id="ad_test",
        started_at=datetime.now(UTC),
        duration_ms=1234,
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=1000),
        plugins=[
            {
                "name": "a.b",
                "candidates_found": 2,
                "candidates": [
                    Candidate(
                        id="x",
                        category="a.b",
                        size_bytes=100,
                        path_or_handle="/x",
                        confidence=Confidence.SAFE,
                        reason="r",
                    ),
                    Candidate(
                        id="y",
                        category="a.b",
                        size_bytes=200,
                        path_or_handle="/y",
                        confidence=Confidence.REVIEW,
                        reason="r",
                    ),
                ],
            }
        ],  # type: ignore[arg-type]
        summary=AuditSummary(
            total_candidates=2,
            total_bytes=300,
            by_confidence={
                "safe": {"count": 1, "bytes": 100},
                "review": {"count": 1, "bytes": 200},
            },
        ),
    )
    lines = _totals_header(report)
    assert any("candidates" in line and "reclaimable" in line for line in lines)
    assert any("SAFE" in line for line in lines)
    assert any("REVIEW" in line for line in lines)


def test_sort_by_size():
    """Sort by size in descending order."""
    candidates = [
        Candidate(id="a", category="c", size_bytes=10, path_or_handle="/a", confidence=Confidence.SAFE, reason=""),
        Candidate(id="b", category="c", size_bytes=100, path_or_handle="/b", confidence=Confidence.SAFE, reason=""),
        Candidate(id="c", category="c", size_bytes=50, path_or_handle="/c", confidence=Confidence.SAFE, reason=""),
    ]
    result = _sort_and_limit(candidates, sort_by="size", top=2)
    assert [c.id for c in result] == ["b", "c"]


def test_sort_by_age():
    """Sort by age (mtime ascending)."""
    now = int(datetime.now(UTC).timestamp())
    candidates = [
        Candidate(
            id="a",
            category="c",
            size_bytes=10,
            path_or_handle="/a",
            confidence=Confidence.SAFE,
            reason="",
            mtime=now - 10,
        ),
        Candidate(
            id="b",
            category="c",
            size_bytes=10,
            path_or_handle="/b",
            confidence=Confidence.SAFE,
            reason="",
            mtime=now - 100,
        ),
    ]
    result = _sort_and_limit(candidates, sort_by="age", top=2)
    assert result[0].id == "b"
    assert result[1].id == "a"


def test_sort_by_tier():
    """Sort by confidence tier."""
    candidates = [
        Candidate(id="m", category="c", size_bytes=10, path_or_handle="/m", confidence=Confidence.MANUAL, reason=""),
        Candidate(id="s", category="c", size_bytes=10, path_or_handle="/s", confidence=Confidence.SAFE, reason=""),
        Candidate(id="r", category="c", size_bytes=10, path_or_handle="/r", confidence=Confidence.REVIEW, reason=""),
    ]
    result = _sort_and_limit(candidates, sort_by="tier")
    assert [c.id for c in result] == ["s", "r", "m"]


def test_format_human_plan():
    c = Candidate(
        id="a", category="docker.image", size_bytes=1500, path_or_handle="x", confidence=Confidence.SAFE, reason="r"
    )
    plan = Plan(audit_id="ad1", candidates=[c])
    text = format_human(plan)
    assert "Plan preview" in text
    assert "docker" in text
    assert "1.5 KB" in text


def test_format_human_run_result():
    c = Candidate(
        id="a", category="docker.image", size_bytes=1500, path_or_handle="x", confidence=Confidence.SAFE, reason="r"
    )
    result = RunResult(
        run_id="run_01",
        audit_id="ad1",
        per_candidate=[(c, {"success": True, "bytes_freed": 1500, "log": "ok"})],
        total_bytes_freed=1500,
    )  # type: ignore[arg-type]
    text = format_human(result)
    assert "run_01" in text
    assert "1.5 KB" in text


def test_format_candidates():
    c = Candidate(
        id="a", category="docker.image", size_bytes=1500, path_or_handle="x", confidence=Confidence.SAFE, reason="r"
    )
    text = format_candidates([c])
    assert "a" in text
    assert "safe" in text
    assert "1.5 KB" in text
    assert "Reason: r" in text


def test_format_json():
    c = Candidate(
        id="a", category="docker.image", size_bytes=1000, path_or_handle="x", confidence=Confidence.SAFE, reason="r"
    )
    plan = Plan(audit_id="ad1", candidates=[c])
    js = format_json(plan)
    data = json.loads(js)
    assert data["audit_id"] == "ad1"


def test_format_md():
    from datetime import datetime

    c = Candidate(
        id="a", category="docker.image", size_bytes=1000, path_or_handle="x", confidence=Confidence.SAFE, reason="r"
    )
    report = AuditReport(
        audit_id="ad1",
        started_at=datetime.now(UTC),
        duration_ms=1000,
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=1024),
        plugins=[
            {
                "name": "docker.images",
                "candidates_found": 1,
                "candidates": [c],
            }
        ],  # type: ignore[arg-type]
        summary=AuditSummary(total_candidates=1, total_bytes=1000, by_confidence={"safe": {"count": 1, "bytes": 1000}}),
    )
    md = format_md(report)
    assert "# Bekas Audit Report" in md
    assert "docker.images" in md
