"""Tests for audit runner."""

from bekas.models import Candidate, Confidence, Context
from bekas.plugin import Plugin
from bekas.runner import run_audit


class FakePlugin(Plugin):
    name = "fake.plugin"
    description = "A fake plugin for testing."

    def discover(self, ctx: Context):
        yield Candidate(
            id="fake:1",
            category="fake.item",
            size_bytes=1024,
            path_or_handle="/tmp/fake_item",
            confidence=Confidence.SAFE,
            reason="Testing",
        )


class CrashPlugin(Plugin):
    name = "crash.plugin"
    description = "Always crashes."

    def discover(self, ctx: Context):
        raise RuntimeError("boom")


def test_run_audit_basic():
    report = run_audit([FakePlugin()], serial=True, profile_name="aggressive")
    assert report.summary.total_candidates == 1
    assert report.summary.total_bytes == 1024


def test_run_audit_crash_isolation():
    report = run_audit([CrashPlugin(), FakePlugin()], serial=True, profile_name="aggressive")
    assert report.summary.total_candidates == 1
