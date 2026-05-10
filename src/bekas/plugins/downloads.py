"""Downloads plugin for bekas."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class DownloadsPlugin(Plugin):
    """Finds old files in the Downloads folder.

    Scans the user's Downloads directory for files that have not been
    accessed in a configurable number of days.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required.
    """

    name = "downloads"
    description = "Finds files in Downloads/ older than a threshold."
    requires_commands = []
    capabilities = Capabilities(quarantine=True, estimated_runtime="fast")

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield old download file candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing old downloaded files.
        """
        downloads = Path.home() / "Downloads"
        if not downloads.exists():
            return
        min_age_days = ctx.config.get("plugin_settings", {}).get("downloads", {}).get("min_age_days", 180)
        cutoff = datetime.now() - timedelta(days=min_age_days)

        for entry in downloads.iterdir():
            if entry.is_dir():
                continue
            try:
                atime = datetime.fromtimestamp(entry.stat().st_atime)
            except OSError:
                continue
            if atime < cutoff:
                size = entry.stat().st_size
                yield Candidate(
                    id=f"dl:{entry.name}",
                    category="downloads.file",
                    size_bytes=size,
                    path_or_handle=str(entry),
                    confidence=Confidence.REVIEW,
                    reason=f"Downloaded file last accessed {min_age_days}+ days ago.",
                    metadata={"path": str(entry), "last_access": atime.isoformat()},
                )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a downloaded file.

        Args:
            candidate: Download file candidate to remove.
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
        """Quarantine a downloaded file.

        Args:
            candidate: Download file candidate to quarantine.
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
