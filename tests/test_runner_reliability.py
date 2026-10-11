"""Regression tests: plugin timeouts, visible failures and a cache that does not go stale."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bekas.config import Profile
from bekas.doctor import _check_plugins
from bekas.formatters import format_human, format_md
from bekas.models import Candidate, Confidence, Context
from bekas.plugin import Plugin, load_plugins
from bekas.runner import _cached_discover, _run_jobs, run_audit


def _cand(path: str, size: int = 1, cid: str = "c:1") -> Candidate:
    return Candidate(
        id=cid, category="fake.test", size_bytes=size, path_or_handle=path, confidence=Confidence.SAFE, reason="r"
    )


class _Quick(Plugin):
    name = "quick.plugin"
    description = "d"

    def discover(self, ctx):
        yield _cand("/nonexistent/quick")


class _Hung(Plugin):
    name = "hung.plugin"
    description = "d"

    def __init__(self) -> None:
        self.release = threading.Event()

    def discover(self, ctx):
        self.release.wait(30)
        yield _cand("/nonexistent/hung", cid="h:1")


class _Boom(Plugin):
    name = "boom.plugin"
    description = "d"

    def discover(self, ctx):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover


@pytest.fixture
def hung():
    p = _Hung()
    yield p
    p.release.set()


# ── timeouts ───────────────────────────────────────────────────


@pytest.mark.parametrize("max_parallel", [1, 16])
def test_hung_plugin_is_reported_and_does_not_block_the_rest(hung, max_parallel):
    start = time.monotonic()
    reports = _run_jobs([hung, _Quick()], Context(), no_cache=True, timeout=0.3, max_parallel=max_parallel)
    assert time.monotonic() - start < 5
    by_name = {r.name: r for r in reports}
    assert by_name["hung.plugin"].error == "timed out after 0.3s"
    assert by_name["hung.plugin"].candidates == []
    assert by_name["quick.plugin"].error is None
    assert by_name["quick.plugin"].candidates_found == 1


def test_late_finish_of_timed_out_plugin_is_harmless(hung):
    reports = _run_jobs([hung, _Quick()], Context(), no_cache=True, timeout=0.2, max_parallel=1)
    assert [r.name for r in reports] == ["hung.plugin", "quick.plugin"]
    hung.release.set()  # the abandoned thread now finishes; must not raise or double-release
    time.sleep(0.2)


def test_reports_keep_plugin_order():
    class A(_Quick):
        name = "a.plugin"

    class B(_Quick):
        name = "b.plugin"

    reports = _run_jobs([B(), A(), _Quick()], Context(), no_cache=True, timeout=5, max_parallel=16)
    assert [r.name for r in reports] == ["b.plugin", "a.plugin", "quick.plugin"]


def test_run_audit_enforces_profile_timeout(hung, monkeypatch):
    profile = Profile(enabled_plugins=["*"], plugin_timeout_seconds=1, min_quiet_hours=0)
    monkeypatch.setattr("bekas.runner.profile_for", lambda n=None: profile)
    start = time.monotonic()
    report = run_audit([hung, _Quick()], no_cache=True)
    assert time.monotonic() - start < 10
    errors = {p.name: p.error for p in report.plugins}
    assert errors["hung.plugin"] == "timed out after 1s"
    assert errors["quick.plugin"] is None


# ── failures are visible ───────────────────────────────────────


def test_crash_is_recorded_in_report_and_json():
    report = run_audit([_Boom(), _Quick()], serial=True, no_cache=True)
    boom = next(p for p in report.plugins if p.name == "boom.plugin")
    assert boom.error == "RuntimeError: kaboom"
    assert report.summary.total_candidates == 1
    assert '"error": "RuntimeError: kaboom"' in report.model_dump_json(indent=2)


def test_human_and_markdown_output_show_failures():
    report = run_audit([_Boom(), _Quick()], serial=True, no_cache=True)
    text = format_human(report)
    assert "boom.plugin failed: RuntimeError: kaboom" in text
    assert "1 plugin(s) did not complete" in text
    assert "`boom.plugin`: RuntimeError: kaboom" in format_md(report)


def test_clean_audit_shows_no_failure_lines():
    text = format_human(run_audit([_Quick()], serial=True, no_cache=True))
    assert "failed" not in text
    assert "did not complete" not in text


def _fake_eps(monkeypatch, items):
    eps = [SimpleNamespace(name=n, load=loader) for n, loader in items]
    monkeypatch.setattr("bekas.plugin.metadata.entry_points", lambda group: eps)


def test_load_plugins_reports_broken_entry_points(monkeypatch):
    def bad_import():
        raise ImportError("no module named nope")

    _fake_eps(
        monkeypatch,
        [("good", lambda: _Quick), ("broken", bad_import), ("notaplugin", lambda: dict)],
    )
    plugins, errors = load_plugins()
    assert [p.name for p in plugins] == ["quick.plugin"]
    assert {e.entry_point: e.error for e in errors} == {
        "broken": "ImportError: no module named nope",
        "notaplugin": "entry point is not a bekas Plugin",
    }


def test_doctor_lists_plugins_that_failed_to_load(monkeypatch):
    def bad_import():
        raise ImportError("nope")

    _fake_eps(monkeypatch, [("good", lambda: _Quick), ("broken", bad_import)])
    result = _check_plugins()
    assert result.status == "warn"
    assert "1 failed to load" in result.message
    assert "broken: ImportError: nope" in result.detail


# ── cache ──────────────────────────────────────────────────────


class _DirPlugin(Plugin):
    name = "dir.plugin"
    description = "d"
    root: Path

    def discover(self, ctx):
        total = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        yield _cand(str(self.root), size=total)


def test_cache_does_not_serve_stale_size_after_nested_growth(tmp_path):
    root = tmp_path / "cache"
    (root / "deep" / "er").mkdir(parents=True)
    (root / "deep" / "er" / "a.bin").write_bytes(b"x" * 10)
    plugin = _DirPlugin()
    plugin.root = root

    first = _cached_discover(plugin, Context(), no_cache=False)
    assert first[0].size_bytes == 10

    # Grows two levels down: the top-level directory's own mtime does not change.
    (root / "deep" / "er" / "b.bin").write_bytes(b"y" * 90)

    second = _cached_discover(plugin, Context(), no_cache=False)
    assert second[0].size_bytes == 100


def test_cache_still_hits_when_nothing_changed(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    (root / "a.bin").write_bytes(b"x" * 10)
    plugin = _DirPlugin()
    plugin.root = root
    _cached_discover(plugin, Context(), no_cache=False)

    class Marker(_DirPlugin):
        def discover(self, ctx):
            yield _cand(str(self.root), size=999)

    marker = Marker()
    marker.root = root
    marker.name = plugin.name
    # Same fingerprint -> the cached candidate (size 10) is returned, not the new one.
    assert _cached_discover(marker, Context(), no_cache=False)[0].size_bytes == 10
