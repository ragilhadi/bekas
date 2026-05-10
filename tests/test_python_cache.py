"""Tests for python_cache plugin."""

from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.python_cache import PythonCachePlugin, _du, _find_caches


def test_find_caches(tmp_path):
    pycache = tmp_path / "project" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "foo.cpython-312.pyc").write_text("x")
    seen: set[Path] = set()
    found = list(_find_caches(tmp_path, seen))
    assert any(pycache.resolve() == f for f in found)


def test_discover_orphan_and_active(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    code = home / "code"
    code.mkdir()

    # Orphan pycache (no project markers)
    orphan = code / "orphan" / "__pycache__"
    orphan.mkdir(parents=True)
    (orphan / "a.pyc").write_text("x")

    # Active project with pyproject.toml (recent) - should NOT yield candidate
    active = code / "active" / "__pycache__"
    active.mkdir(parents=True)
    (active / "b.pyc").write_text("x")
    pkg = code / "active" / "pyproject.toml"
    pkg.write_text("[tool.poetry]\n")
    os = __import__("os")
    os.utime(pkg, ((__import__("time").time(),) * 2))

    # Stale project with pyproject.toml (old) - should yield candidate
    stale = code / "stale" / "__pycache__"
    stale.mkdir(parents=True)
    (stale / "c.pyc").write_text("x")
    stale_pkg = code / "stale" / "pyproject.toml"
    stale_pkg.write_text("[tool.poetry]\n")
    old_mtime = __import__("time").time() - (200 * 24 * 3600)
    os.utime(stale_pkg, (old_mtime, old_mtime))

    p = PythonCachePlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"python.cache": {"min_idle_days": 90}}})
    candidates = list(p.discover(ctx))
    ids = [c.id for c in candidates]
    assert any("orphan" in i for i in ids)
    assert any("stale" in i for i in ids)
    # Active projects do NOT yield candidates
    assert not any("active" in i for i in ids)

    orphan_c = next(c for c in candidates if "orphan" in c.id)
    assert orphan_c.confidence == Confidence.SAFE

    stale_c = next(c for c in candidates if "stale" in c.id)
    assert stale_c.confidence == Confidence.SAFE


def test_remove():
    p = PythonCachePlugin()
    c = Candidate(
        id="pycache:/tmp/fake",
        category="python.cache",
        size_bytes=0,
        path_or_handle="/tmp/fake_pycache",
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=True, config={})
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)


def test_du(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    assert _du(d) == 5
