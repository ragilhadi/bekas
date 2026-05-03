"""System tmp files plugin for bekas."""

from __future__ import annotations

import getpass
import os
import tempfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class SystemTmpPlugin(Plugin):
    """Finds old temporary files the user owns."""

    name = "system.tmp"
    description = "Finds old temporary files owned by the current user."
    requires_commands = []

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        tmp_dirs = [Path(tempfile.gettempdir())]
        # Also scan /tmp on Unix
        if os.name != "nt":
            tmp = Path("/tmp")
            if tmp not in tmp_dirs:
                tmp_dirs.append(tmp)
        min_age_days = ctx.config.get("plugin_settings", {}).get("system.tmp", {}).get("min_age_days", 30)
        cutoff = datetime.now() - timedelta(days=min_age_days)
        current_user = getpass.getuser()

        for tmp in tmp_dirs:
            if not tmp.exists():
                continue
            for entry in tmp.iterdir():
                if entry.is_dir():
                    continue
                try:
                    stat = entry.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                except OSError:
                    continue
                if mtime < cutoff:
                    # Ownership check (best-effort)
                    try:
                        owner = entry.owner()
                    except Exception:
                        owner = current_user
                    if owner != current_user:
                        continue
                    size = stat.st_size
                    yield Candidate(
                        id=f"tmp:{entry.name}",
                        category="system.tmp",
                        size_bytes=size,
                        path_or_handle=str(entry),
                        confidence=Confidence.REVIEW,
                        reason=f"Temp file older than {min_age_days} days.",
                        metadata={"path": str(entry), "mtime": mtime.isoformat()},
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
