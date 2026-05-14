"""Tests for jetbrains_caches plugin."""

from pathlib import Path

from bekas.models import Confidence, Context
from bekas.plugins.jetbrains_caches import JetbrainsCachesPlugin


def test_empty_when_no_cache_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = JetbrainsCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_discovers_old_cache_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache_base = tmp_path / ".cache" / "JetBrains"
    cache_base.mkdir(parents=True)

    latest = cache_base / "IntelliJIdea2024.1"
    latest.mkdir()
    (latest / "file").write_text("x")

    old = cache_base / "PyCharm2023.3"
    old.mkdir()
    (old / "file").write_text("x")

    p = JetbrainsCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "jetbrains.caches"
    assert "PyCharm2023.3" in c.id
    assert c.confidence == Confidence.SAFE
    assert c.size_bytes > 0
