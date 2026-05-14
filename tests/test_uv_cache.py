"""Tests for uv_cache plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Confidence, Context
from bekas.plugins.uv_cache import UvCachePlugin


def test_empty_when_no_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = UvCachePlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_yields_candidate_with_fake_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache" / "uv"
    cache.mkdir(parents=True)
    (cache / "file").write_text("data")

    p = UvCachePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    with patch("shutil.which", return_value="/usr/bin/uv"):
        candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "uv.cache"
    assert c.confidence == Confidence.SAFE
    assert c.size_bytes > 0


def test_yields_review_when_uv_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache" / "uv"
    cache.mkdir(parents=True)
    (cache / "file").write_text("data")

    p = UvCachePlugin()
    ctx = Context(dry_run=True, config={})
    with patch("shutil.which", return_value=None):
        candidates = list(p.discover(ctx))
    assert candidates[0].confidence == Confidence.REVIEW
