"""Tests for pkgmanager_caches plugin.

The plugin reads hardcoded ``/var/cache/...`` paths, so these tests patch
the module-level ``_APT_ARCHIVES`` / ``_DNF_CACHE`` constants to point into
a fresh tmp_path tree.  No root privileges are required; the "writable →
SAFE, root-needed → REVIEW" branch is exercised directly on the patched
paths.
"""

from pathlib import Path

from bekas.models import Confidence, Context, RemovalResult
from bekas.plugins import pkgmanager_caches as pmc
from bekas.plugins.pkgmanager_caches import PkgManagerCachesPlugin, _du, _writable


def test_not_available_when_empty(monkeypatch, tmp_path):
    """When neither cache dir exists, is_available returns False."""
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", tmp_path / "apt" / "archives")
    monkeypatch.setattr(pmc, "_DNF_CACHE", tmp_path / "dnf")
    p = PkgManagerCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)
    assert list(p.discover(ctx)) == []


def _make_apt_archives(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create an apt archives tree; returns (archives, partial, lock, deb)."""
    archives = tmp_path / "apt" / "archives"
    archives.mkdir(parents=True)
    partial = archives / "partial"
    partial.mkdir()
    (partial / "incomplete.deb").write_text("should be skipped")
    lock = archives / "lock"
    lock.write_text("lockfile")
    deb = archives / "curl_1.0_all.deb"
    deb.write_bytes(b"x" * 2048)
    return archives, partial, lock, deb


def test_discover_apt_skips_partial_and_locks(monkeypatch, tmp_path):
    """apt discovery lists only *.deb files, never partial/ or lock files."""
    archives, partial, lock, deb = _make_apt_archives(tmp_path)
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", archives)
    monkeypatch.setattr(pmc, "_DNF_CACHE", tmp_path / "nope")

    p = PkgManagerCachesPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)
    c = next(iter(p.discover(ctx)))

    assert c.category == "apt.cache"
    assert c.id == "apt:archives"
    assert c.size_bytes == 2048
    assert [Path(x).name for x in c.metadata["paths"]] == [deb.name]
    assert [Path(x) for x in c.metadata["paths"]] != [partial]
    assert str(lock) not in c.metadata["paths"]


def test_discover_apt_writable_is_safe(monkeypatch, tmp_path):
    """When the archives dir is writable by the current user, tier is SAFE."""
    archives, *_ = _make_apt_archives(tmp_path)
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", archives)
    monkeypatch.setattr(pmc, "_DNF_CACHE", tmp_path / "nope")

    p = PkgManagerCachesPlugin()
    c = next(iter(p.discover(Context(dry_run=True, config={}))))
    if _writable(archives):  # deterministic on tmp_path under normal umasks
        assert c.confidence == Confidence.SAFE
        assert "root" not in c.reason.lower()


def test_discover_apt_unwritable_is_review(monkeypatch, tmp_path):
    """When the archives dir is not writable, tier is REVIEW and reason mentions root."""
    archives, *_ = _make_apt_archives(tmp_path)
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", archives)
    monkeypatch.setattr(pmc, "_DNF_CACHE", tmp_path / "nope")
    monkeypatch.setattr(pmc.os, "access", lambda *a, **k: False)

    p = PkgManagerCachesPlugin()
    c = next(iter(p.discover(Context(dry_run=True, config={}))))
    assert c.confidence == Confidence.REVIEW
    assert "root" in c.reason.lower()


def test_discover_dnf_tiers(monkeypatch, tmp_path):
    """dnf discovery emits a dnf.cache candidate with tier per writability."""
    cache = tmp_path / "dnf"
    (cache / "mirrors" / "download").mkdir(parents=True)
    (cache / "mirrors" / "download" / "pkg.rpm").write_bytes(b"r" * 512)
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", tmp_path / "nope")
    monkeypatch.setattr(pmc, "_DNF_CACHE", cache)

    p = PkgManagerCachesPlugin()
    c = next(iter(p.discover(Context(dry_run=True, config={}))))
    assert c.category == "dnf.cache"
    assert c.id == "dnf:cache"
    assert c.size_bytes == 512
    assert c.confidence in (Confidence.SAFE, Confidence.REVIEW)

    # Unwritable case
    monkeypatch.setattr(pmc.os, "access", lambda *a, **k: False)
    c2 = next(iter(p.discover(Context(dry_run=True, config={}))))
    assert c2.confidence == Confidence.REVIEW
    assert "root" in c2.reason.lower()


def test_remove_dry_run_leaves_files(monkeypatch, tmp_path):
    """Dry-run removal must not touch any cached files."""
    archives, partial, lock, deb = _make_apt_archives(tmp_path)
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", archives)
    monkeypatch.setattr(pmc, "_DNF_CACHE", tmp_path / "nope")

    p = PkgManagerCachesPlugin()
    ctx = Context(dry_run=True, config={})
    c = next(iter(p.discover(ctx)))
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.log == "dry-run"
    assert deb.exists()
    assert (partial / "incomplete.deb").exists()
    assert lock.exists()


def test_remove_success_deletes_only_debs(monkeypatch, tmp_path):
    """Real apt removal deletes the .deb files, keeps partial/ and locks."""
    archives, partial, lock, deb = _make_apt_archives(tmp_path)
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", archives)
    monkeypatch.setattr(pmc, "_DNF_CACHE", tmp_path / "nope")

    p = PkgManagerCachesPlugin()
    ctx = Context(dry_run=False, config={})
    c = next(iter(p.discover(ctx)))
    result = p.remove(c, ctx)
    assert result.success is True
    assert result.bytes_freed == 2048
    assert not deb.exists()
    assert (partial / "incomplete.deb").exists()
    assert lock.exists()
    assert archives.is_dir()


def test_remove_success_dnf_keeps_dir(monkeypatch, tmp_path):
    """Real dnf removal empties /var/cache/dnf but keeps the directory."""
    cache = tmp_path / "dnf"
    (cache / "mirrors" / "download").mkdir(parents=True)
    (cache / "mirrors" / "download" / "pkg.rpm").write_bytes(b"r" * 512)
    (cache / "metadata.json").write_text("{}")
    monkeypatch.setattr(pmc, "_APT_ARCHIVES", tmp_path / "nope")
    monkeypatch.setattr(pmc, "_DNF_CACHE", cache)

    p = PkgManagerCachesPlugin()
    ctx = Context(dry_run=False, config={})
    c = next(iter(p.discover(ctx)))
    result = p.remove(c, ctx)
    assert result.success is True
    assert result.bytes_freed >= 512
    assert cache.is_dir()
    assert list(cache.iterdir()) == []


def test_du_on_directory(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    (d / "f2").write_text("world")
    assert _du(d) == 10
