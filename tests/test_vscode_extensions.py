"""Tests for vscode_extensions plugin."""

import json
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.vscode_extensions import VscodeExtensionsPlugin


def test_empty_when_no_extensions_json(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ext_dir = tmp_path / ".vscode" / "extensions"
    ext_dir.mkdir(parents=True)
    some_ext = ext_dir / "some-ext"
    some_ext.mkdir(parents=True)
    (some_ext / "package.json").write_text("{}")
    p = VscodeExtensionsPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_yields_orphans_when_extensions_json_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vscode = tmp_path / ".vscode"
    ext_dir = vscode / "extensions"
    ext_dir.mkdir(parents=True)

    orphan = ext_dir / "orphan.ext"
    orphan.mkdir()
    (orphan / "package.json").write_text("{}")

    known = ext_dir / "known.ext"
    known.mkdir()
    (known / "package.json").write_text("{}")

    (vscode / "extensions.json").write_text(json.dumps(["known.ext"]))

    p = VscodeExtensionsPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.id == "vscode:orphan.ext"
    assert c.category == "vscode.extensions.orphan"
    assert c.confidence == Confidence.REVIEW

    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed > 0
    assert not orphan.exists()
    assert known.exists()
    assert result.log == "Deleted"


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_remove_missing_path():
    p = VscodeExtensionsPlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="vscode:orphan.ext",
        category="vscode.extensions.orphan",
        size_bytes=0,
        path_or_handle="/nonexistent/bekas/ext",
        confidence=Confidence.REVIEW,
        reason="t",
        metadata={"path": "/nonexistent/bekas/ext"},
    )
    result = p.remove(c, ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Path does not exist" in result.log


def test_remove_failure_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vscode = tmp_path / ".vscode"
    ext_dir = vscode / "extensions"
    ext_dir.mkdir(parents=True)
    orphan = ext_dir / "orphan.ext"
    orphan.mkdir()
    (orphan / "package.json").write_text("{}")
    (vscode / "extensions.json").write_text(json.dumps(["known.ext"]))

    p = VscodeExtensionsPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(candidates[0], ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert orphan.exists()
