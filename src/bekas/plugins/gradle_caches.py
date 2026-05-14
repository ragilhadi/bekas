"""Gradle caches plugin for bekas.

Discovers Gradle caches in ``~/.gradle/caches``.
Since Gradle caches are fully reproducible, all candidates are SAFE.
There is no quarantine — Gradle automatically re-downloads on demand.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class GradleCachesPlugin(Plugin):
    """Finds and optionally removes Gradle caches.

    Scans ``~/.gradle/caches``.  Since any missing dependency can be
    re-downloaded, items are ``SAFE``.  There is no quarantine.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``gradle`` checked at runtime for tier selection.
    """

    name = "gradle.caches"
    description = "Finds Gradle caches (wrapper and dependency caches)."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if Gradle cache directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if a Gradle cache directory exists.
        """
        return bool(self._cache_paths())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Gradle cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing Gradle caches.
        """
        gradle_detected = shutil.which("gradle") is not None
        tier = Confidence.SAFE if gradle_detected else Confidence.REVIEW

        paths = self._cache_paths()
        total_size = sum(_du(p) for p in paths)
        if total_size == 0:
            return

        yield Candidate(
            id="gradle:caches",
            category="gradle.caches",
            size_bytes=total_size,
            path_or_handle=str(paths[0]) if paths else "",
            confidence=tier,
            reason="Gradle dependency and wrapper caches (recreate on demand).",
            metadata={"paths": [str(p) for p in paths]},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the Gradle cache directories.

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
        """Return discovered Gradle cache directory paths.

        Respects ``GRADLE_USER_HOME`` if set.

        Returns:
            List of existing cache Paths.
        """
        gradle_home = os.environ.get("GRADLE_USER_HOME", "")
        if gradle_home:
            cache = Path(gradle_home) / "caches"
            if cache.exists():
                return [cache]
        cache = Path.home() / ".gradle" / "caches"
        return [cache] if cache.exists() else []


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
