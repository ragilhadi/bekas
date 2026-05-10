"""Tests for python_venvs plugin."""

import time
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.python_venvs import PythonVenvsPlugin, _du, _find_venvs


def test_find_venvs(tmp_path):
    venv = tmp_path / "project" / ".venv"
    venv.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("#!/bin/python")
    seen: set[Path] = set()
    found = list(_find_venvs(tmp_path, seen))
    assert any(venv.resolve() == f for f in found)


def test_discover_orphan_and_active(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    code = home / "code"
    code.mkdir()

    # Orphan venv (no project markers)
    orphan = code / "orphan" / ".venv"
    orphan.mkdir(parents=True)
    (orphan / "bin").mkdir()
    (orphan / "bin" / "python").write_text("#!/bin/python")

    # Active project with pyproject.toml
    active = code / "active" / ".venv"
    active.mkdir(parents=True)
    (active / "bin").mkdir()
    (active / "bin" / "python").write_text("#!/bin/python")
    pkg = code / "active" / "pyproject.toml"
    pkg.write_text("[tool.poetry]\n")
    # Set mtime recent
    new_mtime = time.time()
    os = __import__("os")
    os.utime(pkg, (new_mtime, new_mtime))

    p = PythonVenvsPlugin()
    ctx = Context(dry_run=True, config={"plugin_settings": {"python.venvs": {"min_idle_days": 90}}})
    candidates = list(p.discover(ctx))
    for c in candidates:
        print("CANDIDATE:", c.id, c.confidence, c.reason)
    ids = [c.id for c in candidates]
    assert any("orphan" in i for i in ids)
    assert any("active" in i for i in ids)

    orphan_c = next(c for c in candidates if c.path_or_handle.endswith("orphan/.venv") or "/orphan/.venv" in c.path_or_handle)
    assert orphan_c.confidence == Confidence.SAFE, f"Orphan confidence was {orphan_c.confidence}: {orphan_c.reason}"

    active_c = next(c for c in candidates if c.path_or_handle.endswith("active/.venv"))
    assert active_c.confidence == Confidence.MANUAL


def test_remove():
    p = PythonVenvsPlugin()
    c = Candidate(
        id="venv:/tmp/fake",
        category="python.venv",
        size_bytes=0,
        path_or_handle="/tmp/fake_env",
        confidence=Confidence.SAFE,
        reason="test",
    )
    ctx = Context(dry_run=True, config={})
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)


def test_supports():
    p = PythonVenvsPlugin()
    assert p.supports_quarantine() is True
    assert p.supports_undo() is True


def test_du(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    assert _du(d) == 5
