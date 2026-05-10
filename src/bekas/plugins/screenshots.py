"""Screenshots plugin for bekas."""

from __future__ import annotations

import platform
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class ScreenshotsPlugin(Plugin):
    """Finds old screenshot files.

    Scans platform-specific screenshot folders and flags image files
    older than a configurable threshold.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required.
    """

    name = "screenshots"
    description = "Finds old screenshot files on macOS, Linux, and Windows."
    requires_commands = []
    capabilities = Capabilities(quarantine=True, estimated_runtime="fast")

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield old screenshot candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing outdated screenshots.
        """
        min_age_days = ctx.config.get("plugin_settings", {}).get("screenshots", {}).get("min_age_days", 90)
        cutoff = datetime.now() - timedelta(days=min_age_days)

        folders = self._screenshot_folders()
        for folder in folders:
            if not folder.exists():
                continue
            for entry in folder.iterdir():
                if entry.is_dir():
                    continue
                name_lower = entry.name.lower()
                if "screenshot" in name_lower or name_lower.endswith((".png", ".jpg", ".jpeg")):
                    try:
                        mtime = datetime.fromtimestamp(entry.stat().st_mtime)
                    except OSError:
                        continue
                    if mtime < cutoff:
                        size = entry.stat().st_size
                        yield Candidate(
                            id=f"ss:{entry.name}",
                            category="screenshots.file",
                            size_bytes=size,
                            path_or_handle=str(entry),
                            confidence=Confidence.REVIEW,
                            reason=f"Screenshot older than {min_age_days} days.",
                            metadata={"path": str(entry), "mtime": mtime.isoformat()},
                        )

    def _screenshot_folders(self) -> list[Path]:
        """Return platform-specific screenshot directories.

        Returns:
            List of Paths to search for screenshots.
        """
        system = platform.system().lower()
        home = Path.home()
        if system == "darwin":
            return [home / "Desktop"]
        if system == "windows":
            return [home / "Pictures" / "Screenshots"]
        # Linux and default
        return [home / "Pictures" / "Screenshots"]

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a screenshot file.

        Args:
            candidate: Screenshot candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size = path.stat().st_size
            path.unlink()
            return RemovalResult(success=True, bytes_freed=size, log="Deleted")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def quarantine(
        self,
        candidate: Candidate,
        ctx: Context,
        quarantine_dir: str,
        run_id: str | None = None,
    ) -> RemovalResult:
        """Quarantine a screenshot file.

        Args:
            candidate: Screenshot candidate to quarantine.
            ctx: Execution context.
            quarantine_dir: Directory to store quarantined items.
            run_id: Optional run identifier for tracking.

        Returns:
            Result of the quarantine attempt.
        """
        from bekas.quarantine import move_to_quarantine

        path = Path(candidate.path_or_handle)
        size = path.stat().st_size
        try:
            dest = move_to_quarantine(run_id or "quarantine", path, candidate.category, size, candidate.metadata)
            return RemovalResult(success=True, bytes_freed=size, undo_token=str(dest), log="quarantined")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))
