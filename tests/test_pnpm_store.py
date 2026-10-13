"""Tests for pnpm_store plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Candidate, Confidence, Context, RemovalResult
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


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_discover_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    store = tmp_path / ".local" / "share" / "pnpm" / "store"
    store.mkdir(parents=True)
    (store / "v3" / "pkg.tar").parent.mkdir(parents=True)
    (store / "v3" / "pkg.tar").write_text("x" * 64)

    p = PnpmStorePlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1

    result = p.remove(candidates[0], ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 64
    assert not store.exists()
    assert "Deleted" in result.log


def test_remove_skips_missing_paths(tmp_path):
    p = PnpmStorePlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="pnpm:store",
        category="pnpm.store",
        size_bytes=0,
        path_or_handle=str(tmp_path / "gone"),
        confidence=Confidence.REVIEW,
        reason="t",
        metadata={"paths": [str(tmp_path / "gone")]},
    )
    result = p.remove(c, ctx)
    assert result.success is True
    assert result.bytes_freed == 0


def test_remove_failure_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    store = tmp_path / ".local" / "share" / "pnpm" / "store"
    store.mkdir(parents=True)
    (store / "f").write_text("data")

    p = PnpmStorePlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(candidates[0], ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Failed to delete" in result.log
    assert store.exists()
