"""Tests for python_venvs plugin."""

import time
from pathlib import Path
from unittest.mock import patch

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

    orphan_c = next(c for c in candidates if Path(c.path_or_handle).as_posix().endswith("orphan/.venv"))
    assert orphan_c.confidence == Confidence.SAFE, f"Orphan confidence was {orphan_c.confidence}: {orphan_c.reason}"

    active_c = next(c for c in candidates if Path(c.path_or_handle).as_posix().endswith("active/.venv"))
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


def _make_candidate(path: Path) -> Candidate:
    return Candidate(
        id="venv:t",
        category="python.venv",
        size_bytes=8,
        path_or_handle=str(path),
        confidence=Confidence.SAFE,
        reason="t",
        metadata={"path": str(path)},
    )


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_remove_deletes_venv(tmp_path, monkeypatch):
    venv = tmp_path / "proj" / ".venv"
    venv.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("#!/bin/sh")

    p = PythonVenvsPlugin()
    ctx = Context(dry_run=False, config={})
    result = p.remove(_make_candidate(venv), ctx)
    assert result.success is True
    assert result.bytes_freed > 0
    assert not venv.exists()
    assert result.log == "Deleted"


def test_remove_failure_logged(tmp_path, monkeypatch):
    venv = tmp_path / "proj" / ".venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("venv = Python")

    p = PythonVenvsPlugin()
    ctx = Context(dry_run=False, config={})
    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(_make_candidate(venv), ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert venv.exists()


def test_quarantine_moves_venv(tmp_path):
    venv = tmp_path / "proj" / ".venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("venv = Python")

    p = PythonVenvsPlugin()
    ctx = Context(dry_run=False, config={})
    result = p.quarantine(_make_candidate(venv), ctx, str(tmp_path), run_id="run_test")
    assert result.success is True
    assert result.bytes_freed > 0
    assert result.undo_token
    assert not venv.exists()
    assert Path(result.undo_token).exists()


def test_quarantine_exception(tmp_path, monkeypatch):
    venv = tmp_path / "proj" / ".venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("venv = Python")

    p = PythonVenvsPlugin()
    ctx = Context(dry_run=False, config={})
    with patch("bekas.quarantine.move_to_quarantine", side_effect=RuntimeError("boom")):
        result = p.quarantine(_make_candidate(venv), ctx, str(tmp_path))
    assert result.success is False
    assert result.bytes_freed == 0
    assert "boom" in result.log


def test_du(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    assert _du(d) == 5
