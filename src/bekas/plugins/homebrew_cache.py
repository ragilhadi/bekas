"""Homebrew cache plugin for bekas.

Discovers the Homebrew download cache in the standard per-user cache
location (``~/Library/Caches/Homebrew`` on macOS, or Linuxbrew's
``~/.cache/Homebrew`` on Linux).  Everything in the cache directory is
re-fetchable (bottles, source tarballs, API metadata), so candidates are
SAFE.  There is no quarantine — Homebrew redownloads on demand.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class HomebrewCachePlugin(Plugin):
    """Finds and optionally removes the Homebrew download cache.

    Scans ``~/Library/Caches/Homebrew`` and ``~/.cache/Homebrew``
    (Linuxbrew).  The cache only holds downloaded bottles and source
    archives that ``brew`` re-fetches when needed, so items are ``SAFE``.
    There is no quarantine.
    """

    name = "homebrew.cache"
    description = "Finds the Homebrew download cache."
    requires_commands: list[str] = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if a Homebrew cache directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if a Homebrew cache directory exists on this system.
        """
        return bool(self._cache_paths())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Homebrew cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing the Homebrew cache.
        """
        paths = self._cache_paths()
        if not paths:
            return
        total_size = sum(_du(p) for p in paths)
        if total_size == 0:
            return
        yield Candidate(
            id="homebrew:cache",
            category="homebrew.cache",
            size_bytes=total_size,
            path_or_handle=str(paths[0]),
            confidence=Confidence.SAFE,
            reason="Homebrew download cache (bottles/tarballs refetched on demand).",
            metadata={"paths": [str(p) for p in paths]},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the Homebrew cache directories.

        Args:
            candidate: Cache candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.  In dry-run mode the cache is
            left untouched and ``log="dry-run"`` is returned.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, log="dry-run")
        paths = candidate.metadata.get("paths", []) or [candidate.path_or_handle]
        total_freed = 0
        logs: list[str] = []
        for p_str in paths:
            p = Path(p_str)
            if not p.exists():
                continue
            try:
                freed = _du(p)
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                total_freed += freed
                logs.append(f"Deleted {p_str}")
            except Exception as exc:
                logs.append(f"Failed to delete {p_str}: {exc}")
        success = total_freed > 0 or not logs
        return RemovalResult(
            success=success,
            bytes_freed=total_freed,
            undo_token=None,
            log="; ".join(logs),
        )

    def supports_undo(self) -> bool:
        """Return False because a download cache cannot be undone.

        Returns:
            False.
        """
        return False

    @staticmethod
    def _cache_paths() -> list[Path]:
        """Return discovered Homebrew cache directory paths.

        Returns:
            List of existing cache Paths (macOS and Linuxbrew locations).
        """
        candidates = [
            Path.home() / "Library" / "Caches" / "Homebrew",
            Path.home() / ".cache" / "Homebrew",
        ]
        return [p for p in candidates if p.exists()]


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
