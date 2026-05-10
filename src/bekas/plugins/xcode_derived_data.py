"""Xcode DerivedData plugin for bekas (macOS only).

Scans ``~/Library/Developer/Xcode/DerivedData`` for project build
folders that have not been modified recently.  DerivedData is fully
reproducible, so all candidates are SAFE.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class XcodeDerivedDataPlugin(Plugin):
    """Finds stale Xcode DerivedData project directories.

    Each subdirectory in ``DerivedData`` corresponds to one Xcode project.
    The mtime of ``info.plist`` inside it is used as the last-build timestamp.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        supported_platforms: Only macOS (darwin).
    """

    name = "xcode.derived_data"
    description = "Finds stale Xcode DerivedData project directories."
    requires_commands = []
    supported_platforms = ["darwin"]
    capabilities = Capabilities(platforms=("darwin",), quarantine=False, estimated_runtime="medium")

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield stale DerivedData project directory candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing stale DerivedData projects.
        """
        derived_data = Path.home() / "Library" / "Developer" / "Xcode" / "DerivedData"
        if not derived_data.exists():
            return

        min_idle_days = ctx.config.get("plugin_settings", {}).get("xcode.derived_data", {}).get("min_idle_days", 30)
        cutoff = datetime.now(UTC) - timedelta(days=min_idle_days)

        for entry in derived_data.iterdir():
            if not entry.is_dir():
                continue
            plist = entry / "info.plist"
            try:
                if plist.exists():
                    mtime = datetime.fromtimestamp(plist.stat().st_mtime, tz=UTC)
                else:
                    mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if mtime < cutoff:
                size = _du(entry)
                if size <= 0:
                    continue
                yield Candidate(
                    id=f"xcode:{entry.name}",
                    category="xcode.derived_data",
                    size_bytes=size,
                    path_or_handle=str(entry),
                    confidence=Confidence.SAFE,
                    reason=f"Xcode project not built for {min_idle_days}+ days (DerivedData is reproducible).",
                    metadata={
                        "path": str(entry),
                        "last_build": mtime.isoformat(),
                        "size_bytes": size,
                    },
                )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a DerivedData project directory.

        Args:
            candidate: DerivedData candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size = _du(path)
            shutil.rmtree(path)
            return RemovalResult(success=True, bytes_freed=size, log="Deleted")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return False because undo is not supported.

        Returns:
            False.
        """
        return False


def _du(path: Path) -> int:
    """Compute total byte size of a path recursively.

    Args:
        path: Directory to measure.

    Returns:
        Total size in bytes.
    """
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total
