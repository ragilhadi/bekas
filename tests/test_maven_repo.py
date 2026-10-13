"""Tests for maven_repo plugin."""

import os
import time
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.maven_repo import MavenRepoPlugin


def test_empty_when_no_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = MavenRepoPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_discovers_old_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = tmp_path / ".m2" / "repository" / "mygroup" / "myartifact"
    for ver in ("1.0", "1.1", "1.2"):
        vdir = repo / ver
        vdir.mkdir(parents=True)
        (vdir / "lib.jar").write_text("jar")

    now = time.time()
    os.utime(repo / "1.0", (now - 2000, now - 2000))
    os.utime(repo / "1.1", (now - 1000, now - 1000))
    os.utime(repo / "1.2", (now, now))

    p = MavenRepoPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "maven.repo"
    assert c.id.startswith("maven:myartifact:")
    assert c.confidence in (Confidence.SAFE, Confidence.REVIEW)
    assert c.size_bytes > 0

    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed > 0
    assert not (repo / "1.0").exists()
    assert (repo / "1.2").exists()
    assert result.log == "Deleted"


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_remove_missing_path():
    p = MavenRepoPlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="maven:artifact:1.0",
        category="maven.repo",
        size_bytes=0,
        path_or_handle="/nonexistent/bekas/artifact",
        confidence=Confidence.SAFE,
        reason="t",
        metadata={"path": "/nonexistent/bekas/artifact"},
    )
    result = p.remove(c, ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Path does not exist" in result.log


def test_remove_failure_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = tmp_path / ".m2" / "repository" / "mygroup" / "myartifact"
    for ver in ("1.0", "1.1", "1.2"):
        vdir = repo / ver
        vdir.mkdir(parents=True)
        (vdir / "lib.jar").write_text("jar")
    now = time.time()
    os.utime(repo / "1.0", (now - 2000, now - 2000))
    os.utime(repo / "1.1", (now - 1000, now - 1000))
    os.utime(repo / "1.2", (now, now))

    p = MavenRepoPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(candidates[0], ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert (repo / "1.0").exists()
