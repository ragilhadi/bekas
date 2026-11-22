"""Tests for code_caches (devtool.caches) plugin."""

from pathlib import Path

from bekas.models import Confidence, Context
from bekas.plugins.code_caches import DevtoolCachesPlugin, _du


def _mkcache(base: Path, project: str, cache: str) -> Path:
    """Create a project containing a tool cache with one file."""
    cache_dir = base / project / cache
    cache_dir.mkdir(parents=True)
    (cache_dir / "data.bin").write_text("x" * 100)
    return cache_dir


def test_not_available_when_no_caches(tmp_path, monkeypatch):
    """With no cache dirs present, is_available is False and discover is empty."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = DevtoolCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def test_discover_yields_expected_categories(tmp_path, monkeypatch):
    """Each cache dir yields its matching category and metadata."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "code"
    _mkcache(root, "proj-a", ".pytest_cache")
    _mkcache(root, "proj-b", ".mypy_cache")
    _mkcache(root, "proj-c", ".ruff_cache")

    p = DevtoolCachesPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    assert sorted(c.category for c in candidates) == ["mypy.cache", "pytest.cache", "ruff.cache"]
    assert all(c.confidence == Confidence.SAFE for c in candidates)
    assert all(c.size_bytes == 100 for c in candidates)
    ids = {c.id: c for c in candidates}
    assert (root / "proj-a" / ".pytest_cache") in {Path(c.path_or_handle) for c in candidates}
    assert any("pytest" in c.metadata["tool"] for c in candidates)
    assert not {c.id for c in candidates} - set(ids)  # ids unique across all yielded


def test_discover_prunes_and_respects_depth(tmp_path, monkeypatch):
    """Caches inside node_modules/.git/.venv are ignored; deep caches >6 levels skipped."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "projects"
    _mkcache(root / "app" / "node_modules" / "pkg", "x", ".pytest_cache")
    _mkcache(root / "app" / ".git" / "hooks", "x", ".pytest_cache")
    _mkcache(root / "app" / ".venv" / "lib", "x", ".pytest_cache")

    # Deep but within depth
    deep = root
    for d in ("a", "b", "c", "d"):
        deep = deep / d
    _mkcache(deep, "ok", ".ruff_cache")

    # Too deep: > 6 levels below root
    deep2 = root
    for d in ("1", "2", "3", "4", "5", "6", "7"):
        deep2 = deep2 / d
    _mkcache(deep2, "far", ".pytest_cache")

    p = DevtoolCachesPlugin()
    ctx = Context(dry_run=True, config={})
    paths = {Path(c.path_or_handle) for c in p.discover(ctx)}

    assert deep / "ok" / ".ruff_cache" in paths
    assert deep2 / "far" / ".pytest_cache" not in paths
    assert not any("node_modules" in str(pp) or ".git" in str(pp) or ".venv" in str(pp) for pp in paths)


def test_remove_deletes_cache_dir(tmp_path, monkeypatch):
    """remove() deletes the exact cache dir and reports bytes freed."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    cache = _mkcache(tmp_path / "dev", "proj", ".mypy_cache")
    keep = cache.parent / "src"
    keep.mkdir()
    (keep / "main.py").write_text("keep me")

    p = DevtoolCachesPlugin()
    ctx = Context(dry_run=False, config={})
    cands = list(p.discover(ctx))
    assert len(cands) == 1
    result = p.remove(cands[0], ctx)

    assert result.success is True
    assert result.bytes_freed == 100
    assert not cache.exists()
    assert keep.exists()  # only the cache dir was removed


def test_dry_run_leaves_cache_intact(tmp_path, monkeypatch):
    """With dry_run=True, remove() reports a no-op and keeps the cache."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    cache = _mkcache(tmp_path / "dev", "proj", ".pytest_cache")

    p = DevtoolCachesPlugin()
    ctx = Context(dry_run=True, config={})
    cand = next(iter(p.discover(ctx)))
    result = p.remove(cand, ctx)

    assert result.success is False
    assert cache.exists()
    assert (cache / "data.bin").exists()


def test_du(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "f1").write_text("hello")
    (d / "f2").write_text("world")
    assert _du(d) == 10
