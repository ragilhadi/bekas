"""Pip cache plugin for bekas.

Discovers the pip wheel/http caches in standard cache directories.
Since pip caches are fully reproducible, all candidates are SAFE.
There is no quarantine — pip automatically redownloads on demand.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class PipCachePlugin(Plugin):
    """Finds and optionally removes the pip package cache.

    Scans ``~/.cache/pip`` and ``~/Library/Caches/pip`` for the pip
    wheel/http cache.  Since any missing wheel can be re-downloaded,
    items are ``SAFE``.  There is no quarantine.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``pip`` or ``python`` checked at runtime
            for tier selection.
    """

    name = "pip.cache"
    description = "Finds the pip wheel/http cache."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if pip cache directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if a pip cache directory exists on this system.
        """
        return bool(self._cache_paths())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield pip cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing the pip cache.
        """
        # pip is "detected" if pip or python -m pip works
        pip_detected = shutil.which("pip") is not None or shutil.which("python") is not None
        # For simplicity: if neither pip nor python is on PATH → REVIEW tier
        # (user may be cleaning an old / colleague's machine)
        tier = Confidence.SAFE if pip_detected else Confidence.REVIEW

        paths = self._cache_paths()
        total_size = sum(_du(p) for p in paths)
        if total_size == 0:
            return

        yield Candidate(
            id="pip:cache",
            category="pip.cache",
            size_bytes=total_size,
            path_or_handle=str(paths[0]) if paths else "",
            confidence=tier,
            reason="Pip wheel/http cache (recreate on demand).",
            metadata={"paths": [str(p) for p in paths]},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the pip cache directories.

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
        """Return discovered pip cache directory paths.

        Returns:
            List of existing cache Paths.
        """
        candidates = [
            Path.home() / ".cache" / "pip",
            Path.home() / "Library" / "Caches" / "pip",
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
