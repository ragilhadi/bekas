"""Cargo registry cache plugin for bekas.

Discovers the Cargo registry cache in ``~/.cargo/registry/cache``.
Since Cargo caches are fully reproducible, all candidates are SAFE.
There is no quarantine — Cargo automatically re-downloads on demand.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class CargoRegistryPlugin(Plugin):
    """Finds and optionally removes the Cargo registry cache.

    Scans ``~/.cargo/registry/cache``.  Since any missing crate can be
    re-downloaded from crates.io, items are ``SAFE``.  There is no
    quarantine.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``cargo`` checked at runtime for tier selection.
    """

    name = "cargo.registry"
    description = "Finds the Cargo registry cache."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if Cargo registry cache directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if a Cargo registry cache directory exists.
        """
        return bool(self._cache_paths())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Cargo registry cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing the Cargo registry cache.
        """
        cargo_detected = shutil.which("cargo") is not None
        tier = Confidence.SAFE if cargo_detected else Confidence.REVIEW

        paths = self._cache_paths()
        total_size = sum(_du(p) for p in paths)
        if total_size == 0:
            return

        yield Candidate(
            id="cargo:registry",
            category="cargo.registry",
            size_bytes=total_size,
            path_or_handle=str(paths[0]) if paths else "",
            confidence=tier,
            reason="Cargo registry cache (recreate on demand via cargo fetch).",
            metadata={"paths": [str(p) for p in paths]},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the Cargo registry cache directories.

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
        """Return discovered Cargo registry cache directory paths.

        Returns:
            List of existing cache Paths.
        """
        cargo_home = os.environ.get("CARGO_HOME", "")
        if cargo_home:
            cache = Path(cargo_home) / "registry" / "cache"
            if cache.exists():
                return [cache]
        cache = Path.home() / ".cargo" / "registry" / "cache"
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
