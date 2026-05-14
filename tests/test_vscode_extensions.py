"""Tests for vscode_extensions plugin."""

import json
from pathlib import Path

from bekas.models import Confidence, Context
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
