"""PNPM store plugin for bekas.

Discovers the PNPM global content-addressable store.
Removing the global store will break linked projects until the next
install.  Default tier ``REVIEW`` per plan.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class PnpmStorePlugin(Plugin):
    """Finds and optionally removes the PNPM global store.

    Scans ``~/.local/share/pnpm/store`` and ``~/Library/pnpm/store``.
    Since removing the global store will break linked projects until the
    next install, tier defaults to ``REVIEW``.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``pnpm`` checked at runtime for tier selection.
    """

    name = "pnpm.store"
    description = "Finds the PNPM global content-addressable store."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if PNPM store directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if a PNPM store directory exists.
        """
        return bool(self._store_paths())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield PNPM store candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing the PNPM store.
        """
        pnpm_detected = shutil.which("pnpm") is not None
        # Even if pnpm is detected, this is content-addressable and breaks
        # linked projects until next install → REVIEW tier per plan.
        _ = pnpm_detected
        tier = Confidence.REVIEW

        paths = self._store_paths()
        total_size = sum(_du(p) for p in paths)
        if total_size == 0:
            return

        yield Candidate(
            id="pnpm:store",
            category="pnpm.store",
            size_bytes=total_size,
            path_or_handle=str(paths[0]) if paths else "",
            confidence=tier,
            reason="PNPM global store. Removing it breaks linked projects until next pnpm install.",
            metadata={"paths": [str(p) for p in paths]},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the PNPM store directories.

        Args:
            candidate: Store candidate to remove.
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
        """Return False because store cannot be undone.

        Returns:
            False.
        """
        return False

    @staticmethod
    def _store_paths() -> list[Path]:
        """Return discovered PNPM store directory paths.

        Returns:
            List of existing store Paths.
        """
        candidates = [
            Path.home() / ".local" / "share" / "pnpm" / "store",
            Path.home() / "Library" / "pnpm" / "store",
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
