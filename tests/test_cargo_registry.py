"""Tests for cargo_registry plugin."""

from pathlib import Path
from unittest.mock import patch

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.cargo_registry import CargoRegistryPlugin


def test_empty_when_no_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = CargoRegistryPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_yields_candidate_with_fake_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cargo" / "registry" / "cache"
    cache.mkdir(parents=True)
    (cache / "crate").write_text("data")

    p = CargoRegistryPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)

    with patch("shutil.which", return_value="/usr/bin/cargo"):
        candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "cargo.registry"
    assert c.confidence == Confidence.SAFE
    assert c.size_bytes > 0


def test_cargo_home_override(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cargo_home = tmp_path / "custom_cargo"
    cache = cargo_home / "registry" / "cache"
    cache.mkdir(parents=True)
    (cache / "crate").write_text("data")
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))

    p = CargoRegistryPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    assert str(cache) in candidates[0].path_or_handle or any(
        str(cargo_home) in p for p in candidates[0].metadata.get("paths", [])
    )


def _boom(*args, **kwargs):
    raise OSError("denied")


def test_discover_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cargo" / "registry" / "cache"
    cache.mkdir(parents=True)
    (cache / "registry" / "crate.crate").parent.mkdir(parents=True)
    (cache / "registry" / "crate.crate").write_text("x" * 64)

    p = CargoRegistryPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1

    result = p.remove(candidates[0], ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 64
    assert not cache.exists()
    assert "Deleted" in result.log


def test_remove_skips_missing_paths(tmp_path):
    p = CargoRegistryPlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="cargo:registry",
        category="cargo.registry",
        size_bytes=0,
        path_or_handle=str(tmp_path / "gone"),
        confidence=Confidence.SAFE,
        reason="t",
        metadata={"paths": [str(tmp_path / "gone")]},
    )
    result = p.remove(c, ctx)
    assert result.success is True
    assert result.bytes_freed == 0


def test_remove_failure_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cargo" / "registry" / "cache"
    cache.mkdir(parents=True)
    (cache / "f").write_text("data")

    p = CargoRegistryPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    monkeypatch.setattr("shutil.rmtree", _boom)
    result = p.remove(candidates[0], ctx)
    assert result.success is False
    assert result.bytes_freed == 0
    assert "Failed to delete" in result.log
    assert cache.exists()
