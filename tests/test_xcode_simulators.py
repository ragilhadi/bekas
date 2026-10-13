"""Tests for xcode_simulators plugin."""

import json
import platform
import subprocess
from unittest.mock import MagicMock

import pytest

from bekas.models import Candidate, Confidence, Context
from bekas.plugins import xcode_simulators
from bekas.plugins.xcode_simulators import (
    XcodeSimulatorsPlugin,
    _list_unavailable_runtimes,
)


def test_not_available_on_non_darwin():
    if platform.system().lower() == "darwin":
        pytest.skip("macOS-only test")
    p = XcodeSimulatorsPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)


def test_plugin_properties():
    p = XcodeSimulatorsPlugin()
    assert p.name == "xcode.simulators"
    assert p.description
    assert p.capabilities.platforms == ("darwin",)
    assert p.supports_undo() is False
    assert p.supports_quarantine() is False


def _runtime(bundle_missing: bool = True, size: int = 1024):
    return {
        "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-17-0",
        "name": "iOS 17.0",
        "version": "17.0",
        "bundlePath": "/nonexistent/bekas/iOS" if bundle_missing else "/tmp",
        "availability": "unavailable" if bundle_missing else "",
        "size_bytes": size,
    }


def _mock_run(returncode=0, payload=None, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout or (json.dumps(payload) if payload else ""), stderr=stderr)


def test_discover_yields_unavailable_runtimes(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_run(0, {"runtimes": [_runtime()]}))
    p = XcodeSimulatorsPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.id == "xcode:com.apple.CoreSimulator.SimRuntime.iOS-17-0"
    assert c.category == "xcode.simulators"
    assert c.confidence == Confidence.SAFE
    assert c.size_bytes == 1024
    assert c.metadata["runtime_id"] == "com.apple.CoreSimulator.SimRuntime.iOS-17-0"


def test_discover_uses_size_estimate_when_missing(monkeypatch):
    runtime = _runtime()
    del runtime["size_bytes"]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_run(0, {"runtimes": [runtime]}))
    p = XcodeSimulatorsPlugin()
    candidates = list(p.discover(Context(dry_run=True, config={})))
    assert len(candidates) == 1
    assert candidates[0].size_bytes == 5 * 1024 * 1024 * 1024


def test_discover_skips_available_runtimes(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_run(0, {"runtimes": [_runtime(bundle_missing=False)]}))
    assert _list_unavailable_runtimes() == []


def test_discover_empty_when_no_runtimes(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_run(0, {"runtimes": []}))
    assert _list_unavailable_runtimes() == []


def test_list_unavailable_runtimes_returncode_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_run(returncode=1, stderr="boom"))
    assert _list_unavailable_runtimes() == []


def test_list_unavailable_runtimes_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("no xcrun")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _list_unavailable_runtimes() == []


def _make_candidate(size_bytes: int = 1024) -> Candidate:
    runtime_id = "com.apple.CoreSimulator.SimRuntime.iOS-17-0"
    return Candidate(
        id=f"xcode:{runtime_id}",
        category="xcode.simulators",
        size_bytes=size_bytes,
        path_or_handle=runtime_id,
        confidence=Confidence.SAFE,
        reason="t",
        metadata={"runtime_id": runtime_id},
    )


def test_remove_dry_run():
    p = XcodeSimulatorsPlugin()
    ctx = Context(dry_run=True, config={})
    result = p.remove(_make_candidate(), ctx)
    assert result.success is True
    assert result.bytes_freed == 0
    assert result.log == "dry-run"


def test_remove_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_run(returncode=0))
    p = XcodeSimulatorsPlugin()
    ctx = Context(dry_run=False, config={})
    result = p.remove(_make_candidate(4096), ctx)
    assert result.success is True
    assert result.bytes_freed == 4096
    assert "Deleted runtime" in result.log


def test_remove_xcrun_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _mock_run(returncode=1, stderr="boom"))
    p = XcodeSimulatorsPlugin()
    ctx = Context(dry_run=False, config={})
    result = p.remove(_make_candidate(), ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "xcrun failed" in result.log


def test_remove_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("no xcrun")

    monkeypatch.setattr(subprocess, "run", _raise)
    p = XcodeSimulatorsPlugin()
    ctx = Context(dry_run=False, config={})
    result = p.remove(_make_candidate(), ctx)
    assert result.success is False
    assert result.bytes_freed == 0


def test_path_exists():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        assert xcode_simulators._path_exists(d) is True
    assert xcode_simulators._path_exists("/nonexistent/bekas") is False
