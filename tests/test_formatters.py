"""Tests for output formatters."""

from __future__ import annotations

import json
from datetime import UTC

from bekas.formatters import _human_size, format_candidates, format_human, format_json, format_md
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
