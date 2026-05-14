"""Tests for pnpm_store plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Confidence, Context
from bekas.plugins.pnpm_store import PnpmStorePlugin


def test_empty_when_no_store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = PnpmStorePlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_yields_candidate_with_fake_store(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    store = tmp_path / ".local" / "share" / "pnpm" / "store"
    store.mkdir(parents=True)
    (store / "file").write_text("data")

    p = PnpmStorePlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    with patch("shutil.which", return_value="/usr/bin/pnpm"):
        candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "pnpm.store"
    assert c.confidence == Confidence.REVIEW
    assert c.size_bytes > 0
