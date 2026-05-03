"""Screenshots plugin for bekas."""

from __future__ import annotations

import platform
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class ScreenshotsPlugin(Plugin):
    """Finds old screenshot files."""

    name = "screenshots"
    description = "Finds old screenshot files on macOS, Linux, and Windows."
    requires_commands = []

    def discover(self, ctx: Context) -> Iterator[Candidate]:
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
        system = platform.system().lower()
        home = Path.home()
        if system == "darwin":
            return [home / "Desktop"]
        if system == "windows":
            return [home / "Pictures" / "Screenshots"]
        # Linux and default
        return [home / "Pictures" / "Screenshots"]

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

    def quarantine(self, candidate: Candidate, ctx: Context, quarantine_dir: str, run_id: str | None = None) -> RemovalResult:
        from bekas.quarantine import move_to_quarantine

        path = Path(candidate.path_or_handle)
        size = path.stat().st_size
        try:
            dest = move_to_quarantine(run_id or "quarantine", path, candidate.category, size, candidate.metadata)
            return RemovalResult(success=True, bytes_freed=size, undo_token=str(dest), log="quarantined")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))
