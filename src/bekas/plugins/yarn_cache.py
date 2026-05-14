"""Yarn cache plugin for bekas.

Discovers the Yarn global cache in standard cache directories.
Yarn Berry is content-addressable; removing the cache will break linked
projects until the next install.  Default tier ``REVIEW`` per plan.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class YarnCachePlugin(Plugin):
    """Finds and optionally removes the Yarn global cache.

    Scans ``~/.cache/yarn`` and ``~/Library/Caches/Yarn``.
    Since removing the global cache will break linked projects until the
    next install, tier defaults to ``REVIEW``.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``yarn`` checked at runtime for tier selection.
    """

    name = "yarn.cache"
    description = "Finds the Yarn global cache."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if Yarn cache directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if a Yarn cache directory exists.
        """
        return bool(self._cache_paths())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Yarn cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing the Yarn cache.
        """
        yarn_detected = shutil.which("yarn") is not None
        _ = yarn_detected
        # Yarn Berry is content-addressable; removing cache breaks linked projects
        tier = Confidence.REVIEW

        paths = self._cache_paths()
        total_size = sum(_du(p) for p in paths)
        if total_size == 0:
            return

        yield Candidate(
            id="yarn:cache",
            category="yarn.cache",
            size_bytes=total_size,
            path_or_handle=str(paths[0]) if paths else "",
            confidence=tier,
            reason="Yarn global cache. Removing it breaks linked projects until next yarn install.",
            metadata={"paths": [str(p) for p in paths]},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the Yarn cache directories.

        Args:
            candidate: Cache candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
        paths = candidate.metadata.get("paths", [])
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
        """Return False because cache cannot be undone.

        Returns:
            False.
        """
        return False

    @staticmethod
    def _cache_paths() -> list[Path]:
        """Return discovered Yarn cache directory paths.

        Returns:
            List of existing cache Paths.
        """
        candidates = [
            Path.home() / ".cache" / "yarn",
            Path.home() / "Library" / "Caches" / "Yarn",
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
