"""Tests for jetbrains_caches plugin."""

from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
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


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_discover_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache_base = tmp_path / ".cache" / "JetBrains"
    latest = cache_base / "IntelliJIdea2024.1"
    latest.mkdir(parents=True)
    (latest / "file").write_text("x")
    cache = cache_base / "IntelliJIdea2023.2"
    cache.mkdir(parents=True)
    (cache / "caches" / "data.bin").parent.mkdir(parents=True)
    (cache / "caches" / "data.bin").write_text("x" * 64)

    p = JetbrainsCachesPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    ids = [c.id for c in candidates]
    assert any("IntelliJIdea2023.2" in i for i in ids)
    target = next(c for c in candidates if "IntelliJIdea2023.2" in c.id)

    result = p.remove(target, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 64
    assert not cache.exists()
    assert latest.exists()
    assert result.log == "Deleted"


def test_remove_missing_path():
    p = JetbrainsCachesPlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="jetbrains:IntelliJIdea2023.2",
        category="jetbrains.caches",
        size_bytes=0,
        path_or_handle="/nonexistent/bekas/idea",
        confidence=Confidence.SAFE,
        reason="t",
        metadata={"path": "/nonexistent/bekas/idea"},
    )
    result = p.remove(c, ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Path does not exist" in result.log


def test_remove_failure_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache_base = tmp_path / ".cache" / "JetBrains"
    latest = cache_base / "IntelliJIdea2024.1"
    latest.mkdir(parents=True)
    (latest / "file").write_text("x")
    cache = cache_base / "IntelliJIdea2023.2"
    cache.mkdir(parents=True)
    (cache / "file").write_text("x")

    p = JetbrainsCachesPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = [c for c in p.discover(ctx) if "IntelliJIdea2023.2" in c.id]

    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(candidates[0], ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert cache.exists()
