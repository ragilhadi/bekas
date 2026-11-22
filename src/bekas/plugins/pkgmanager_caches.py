"""System package manager cache plugin for bekas.

Discovers downloaded package archives kept by apt (``/var/cache/apt/archives``,
``*.deb`` files, skipping the ``partial`` subdirectory and lock files) and
dnf (``/var/cache/dnf``).  Deleting these caches is normally safe — the
archives are refetched — but removing them from ``/var/cache`` requires
root, so the tier is ``REVIEW`` with a reason that mentions root.  When the
enclosing directory is writable by the current user (e.g. in test sandboxes
or root-run sessions) the tier stays ``SAFE``.  There is no quarantine —
package archives are re-downloadable.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin

_APT_ARCHIVES = Path("/var/cache/apt/archives")
_DNF_CACHE = Path("/var/cache/dnf")


def _writable(path: Path) -> bool:
    """Return True if *path* exists and the current user can write to it.

    Uses ``os.access`` so the check reflects real filesystem permissions.

    Args:
        path: Directory to test.

    Returns:
        True if the directory exists and is writable by the current user.
    """
    try:
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


class PkgManagerCachesPlugin(Plugin):
    """Finds apt and dnf package caches under ``/var/cache``.

    Emits one candidate per package-manager cache found:

    - ``apt.cache``: ``/var/cache/apt/archives`` ``*.deb`` files.  The
      ``partial/`` subdirectory, lock files (``lock``, ``*.lock``) and the
      ``pkgcache.bin``/``srcpkgcache.bin`` metadata are excluded.
    - ``dnf.cache``: ``/var/cache/dnf`` (downloaded packages and metadata).

    Confidence: ``SAFE`` when the enclosing directory is writable by the
    current user; ``REVIEW`` (with a root note in ``reason``) when root is
    needed to delete.  There is no quarantine.
    """

    name = "pkgmanager.caches"
    description = "Finds apt and dnf package caches under /var/cache."
    requires_commands: list[str] = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")
    categories = ("apt.cache", "dnf.cache")

    def is_available(self, ctx: Context) -> bool:
        """Check if any supported package-manager cache exists.

        Args:
            ctx: Execution context.

        Returns:
            True if the apt archives or dnf cache directory exists.
        """
        return _APT_ARCHIVES.exists() or _DNF_CACHE.exists()

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield apt and dnf cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects, one per package-manager cache found.
        """
        yield from self._discover_apt()
        yield from self._discover_dnf()

    def _discover_apt(self) -> Iterator[Candidate]:
        """Yield the apt archives candidate if any ``*.deb`` files exist.

        Yields:
            Candidate for ``/var/cache/apt/archives`` ``*.deb`` files.
        """
        archives = _APT_ARCHIVES
        if not archives.is_dir():
            return
        debs: list[Path] = []
        for entry in sorted(archives.iterdir()):
            name = entry.name
            if name == "partial" or name == "lock" or name.endswith(".lock"):
                continue
            if name in ("pkgcache.bin", "srcpkgcache.bin"):
                continue
            if entry.is_file() and name.endswith(".deb"):
                debs.append(entry)
        if not debs:
            return
        size = sum(_du(p) for p in debs)
        if size == 0:
            return
        writable = _writable(archives)
        tier = Confidence.SAFE if writable else Confidence.REVIEW
        reason = "Downloaded .deb archives (refetched on demand)."
        if not writable:
            reason += " Deleting requires root privileges."
        yield Candidate(
            id="apt:archives",
            category="apt.cache",
            size_bytes=size,
            path_or_handle=str(archives),
            confidence=tier,
            reason=reason,
            metadata={"paths": [str(p) for p in debs]},
        )

    def _discover_dnf(self) -> Iterator[Candidate]:
        """Yield the dnf cache candidate if the cache directory exists.

        Yields:
            Candidate for ``/var/cache/dnf``.
        """
        cache = _DNF_CACHE
        if not cache.is_dir():
            return
        size = _du(cache)
        if size == 0:
            return
        writable = _writable(cache)
        tier = Confidence.SAFE if writable else Confidence.REVIEW
        reason = "dnf download/metadata cache (refetched on demand)."
        if not writable:
            reason += " Deleting requires root privileges."
        yield Candidate(
            id="dnf:cache",
            category="dnf.cache",
            size_bytes=size,
            path_or_handle=str(cache),
            confidence=tier,
            reason=reason,
            metadata={},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the candidate's cached files.

        Only the exact discovered targets are deleted: for apt the listed
        ``*.deb`` files (never the enclosing ``archives`` directory or the
        ``partial`` dir/locks), for dnf the contents of ``/var/cache/dnf``
        but not the directory itself.

        Args:
            candidate: Cache candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.  In dry-run mode nothing is
            modified and ``log="dry-run"`` is returned.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, log="dry-run")
        try:
            if candidate.id == "apt:archives":
                return self._remove_paths(candidate.metadata.get("paths", []))
            if candidate.id == "dnf:cache":
                return self._remove_children(Path(candidate.path_or_handle))
            return RemovalResult(success=False, bytes_freed=0, log="Unknown candidate id")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def _remove_paths(self, paths: list[str]) -> RemovalResult:
        """Delete an explicit list of files.

        Args:
            paths: Candidate file paths to delete.

        Returns:
            Aggregated deletion result.
        """
        return self._remove_iter(Path(p) for p in paths)

    def _remove_children(self, directory: Path) -> RemovalResult:
        """Delete the *contents* of a directory, keeping the directory.

        Args:
            directory: Directory whose children should be deleted.

        Returns:
            Aggregated deletion result.
        """
        if not directory.is_dir():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        return self._remove_iter(e for e in directory.iterdir())

    @staticmethod
    def _remove_iter(paths: Iterator[Path]) -> RemovalResult:
        """Best-effort delete each path, aggregating a result.

        Args:
            paths: Iterable of paths to delete.

        Returns:
            Aggregated deletion result; the last error wins in the log.
        """
        total_freed = 0
        logs: list[str] = []
        for p in paths:
            if not p.exists() and not p.is_symlink():
                continue
            try:
                freed = _du(p)
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                total_freed += freed
                logs.append(f"Deleted {p}")
            except Exception as exc:
                logs.append(f"Failed to delete {p}: {exc}")
        success = total_freed > 0 or not logs
        return RemovalResult(
            success=success,
            bytes_freed=total_freed,
            undo_token=None,
            log="; ".join(logs),
        )

    def supports_undo(self) -> bool:
        """Return False because package archives are refetched, not restored.

        Returns:
            False.
        """
        return False


def _du(path: Path) -> int:
    """Compute total byte size of a path recursively.

    Args:
        path: File or directory to measure.

    Returns:
        Total size in bytes.
    """
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return total
