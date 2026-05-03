"""Tests for core models."""

from bekas.models import Candidate, Confidence, Plan


def test_candidate_creation():
    c = Candidate(
        id="test:1",
        category="docker.image",
        size_bytes=1024,
        path_or_handle="docker://abc",
        confidence=Confidence.SAFE,
        reason="Dangling image",
    )
    assert c.confidence == Confidence.SAFE
    assert c.size_bytes == 1024


def test_plan_totals():
    c1 = Candidate(
        id="a", category="foo", size_bytes=100, path_or_handle="/a",
        confidence=Confidence.SAFE, reason="r",
    )
    c2 = Candidate(
        id="b", category="bar", size_bytes=200, path_or_handle="/b",
        confidence=Confidence.REVIEW, reason="r",
    )
    plan = Plan(audit_id="ad_1", candidates=[c1, c2])
    assert plan.total_bytes() == 300
    assert len(plan.by_category()) == 2
    assert len(plan.by_confidence()[Confidence.SAFE]) == 1
