"""Tests for yarn_cache plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Confidence, Context
from bekas.plugins.yarn_cache import YarnCachePlugin


def test_empty_when_no_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = YarnCachePlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_yields_candidate_with_fake_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache" / "yarn"
    cache.mkdir(parents=True)
    (cache / "file").write_text("data")

    p = YarnCachePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    with patch("shutil.which", return_value="/usr/bin/yarn"):
        candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "yarn.cache"
    assert c.confidence == Confidence.REVIEW
    assert c.size_bytes > 0
