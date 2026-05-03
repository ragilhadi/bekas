"""Downloads plugin for bekas."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class DownloadsPlugin(Plugin):
    """Finds old files in the Downloads folder."""

    name = "downloads"
    description = "Finds files in Downloads/ older than a threshold."
    requires_commands = []

    def discover(self, ctx: Context) -> Iterator[Candidate]:
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
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size = path.stat().st_size
            path.unlink()
            return RemovalResult(success=True, bytes_freed=size, log="Deleted")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_quarantine(self) -> bool:
        return True

    def quarantine(self, candidate: Candidate, ctx: Context, quarantine_dir: str) -> RemovalResult:
        from bekas.quarantine import move_to_quarantine

        path = Path(candidate.path_or_handle)
        size = path.stat().st_size
        try:
            dest = move_to_quarantine("quarantine", path, candidate.category, size, candidate.metadata)
            return RemovalResult(success=True, bytes_freed=size, undo_token=str(dest), log="quarantined")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))
