"""Tests for pip_cache plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Confidence, Context, RemovalResult
from bekas.plugins.pip_cache import PipCachePlugin, _du


def test_cache_paths_empty_when_no_dirs(monkeypatch, tmp_path):
    """When no pip cache dirs exist, is_available returns False and paths is empty."""
    p = PipCachePlugin()
    # Override home to a temporary dir so real caches aren't found
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)


def test_discover_and_remove(tmp_path, monkeypatch):
    """Create a fake pip cache dir, discover it, then remove it."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cache = home / ".cache" / "pip"
    cache.mkdir(parents=True)
    (cache / "wheels").mkdir()
    (cache / "wheels" / "fake.whl").write_text("wheel data")

    p = PipCachePlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "pip.cache"
    assert c.confidence in (Confidence.SAFE, Confidence.REVIEW)

    # Fake shutil.which so pip appears detected -> SAFE
    with patch("shutil.which", return_value="/usr/bin/pip"):
        candidates = list(p.discover(ctx))
        assert candidates[0].confidence == Confidence.SAFE

    # Remove
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert not cache.exists()


def test_du_on_directory(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    (d / "f2").write_text("world")
    assert _du(d) == 10  # 5 + 5
