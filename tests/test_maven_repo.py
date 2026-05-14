"""Tests for maven_repo plugin."""

import os
import time
from pathlib import Path

from bekas.models import Confidence, Context
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
