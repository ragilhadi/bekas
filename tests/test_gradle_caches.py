"""Tests for gradle_caches plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Confidence, Context
from bekas.plugins.gradle_caches import GradleCachesPlugin


def test_empty_when_no_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = GradleCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_yields_candidate_with_fake_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".gradle" / "caches"
    cache.mkdir(parents=True)
    (cache / "file").write_text("data")

    p = GradleCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    with patch("shutil.which", return_value="/usr/bin/gradle"):
        candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "gradle.caches"
    assert c.confidence == Confidence.SAFE
    assert c.size_bytes > 0


def test_gradle_user_home_override(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    gradle_home = tmp_path / "custom_gradle"
    cache = gradle_home / "caches"
    cache.mkdir(parents=True)
    (cache / "file").write_text("data")
    monkeypatch.setenv("GRADLE_USER_HOME", str(gradle_home))

    p = GradleCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    assert str(cache) in candidates[0].metadata.get("paths", [])
