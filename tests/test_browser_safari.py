"""Tests for browser_safari plugin."""

import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.browser_safari import BrowserSafariPlugin


def test_not_available_on_non_darwin():
    if platform.system().lower() == "darwin":
        pytest.skip("macOS-only test")
    p = BrowserSafariPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)


def test_plugin_properties():
    p = BrowserSafariPlugin()
    assert p.name == "browser.safari"
    assert p.description
    assert p.capabilities.platforms == ("darwin",)


def _make_candidate(path: Path) -> Candidate:
    return Candidate(
        id="safari:cache",
        category="browser.safari",
        size_bytes=64,
        path_or_handle=str(path),
        confidence=Confidence.REVIEW,
        reason="t",
        metadata={"path": str(path)},
    )


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_discover_and_remove_on_fake_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / "Library" / "Caches" / "com.apple.Safari"
    cache.mkdir(parents=True)
    (cache / "f.x" / "blob").parent.mkdir(parents=True)
    (cache / "f.x" / "blob").write_text("x" * 64)

    p = BrowserSafariPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    assert candidates[0].confidence == Confidence.REVIEW

    result = p.remove(candidates[0], ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 64
    assert not cache.exists()
    assert result.log == "Deleted"


def test_remove_missing_path(tmp_path):
    p = BrowserSafariPlugin()
    ctx = Context(dry_run=True, config={})
    result = p.remove(_make_candidate(tmp_path / "gone"), ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Path does not exist" in result.log


def test_remove_failure_logged(tmp_path, monkeypatch):
    cache = tmp_path / "safari-cache"
    cache.mkdir()
    (cache / "blob").write_text("x" * 8)

    p = BrowserSafariPlugin()
    ctx = Context(dry_run=True, config={})
    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(_make_candidate(cache), ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert cache.exists()


def test_remove_runs_on_any_platform(tmp_path):
    """remove() has no platform gate, so it works from a constructed candidate anywhere."""
    if platform.system().lower() == "darwin":
        pytest.skip("covered on non-mac platforms")
    cache = tmp_path / "Library" / "Caches" / "com.apple.Safari"
    cache.mkdir(parents=True)
    (cache / "f").write_text("x" * 8)

    p = BrowserSafariPlugin()
    ctx = Context(dry_run=True, config={})
    with patch("bekas.plugins.browser_safari._du", return_value=8):
        result = p.remove(_make_candidate(cache), ctx)
    assert result.success is True
    assert result.bytes_freed == 8
