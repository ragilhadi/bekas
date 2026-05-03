"""Dotfiles backup files plugin for bekas."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class DotfilesBackupsPlugin(Plugin):
    """Finds old dotfile backup files."""

    name = "dotfiles.backups"
    description = "Finds old dotfile backup files like .zshrc.backup-*, .bashrc.bak, etc."
    requires_commands = []

    # Patterns for backup files
    PATTERNS = [
        ".*backup*",
        ".*bak",
        ".*bak.*",
        ".*old",
        ".*old.*",
        ".*orig",
        ".*save",
        ".*swp",
        ".*swo",
        "*~",
    ]

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        home = Path.home()
        for item in home.iterdir():
            if item.is_dir():
                continue
            name = item.name
            if name.startswith(".") and any(fnmatch.fnmatch(name.lower(), p.lower()) for p in self.PATTERNS):
                size = item.stat().st_size
                yield Candidate(
                    id=f"dotfile:{name}",
                    category="dotfiles.backups",
                    size_bytes=size,
                    path_or_handle=str(item),
                    confidence=Confidence.SAFE,
                    reason=f"Backup file matching pattern: {name}",
                    metadata={"filename": name},
                )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="File does not exist")
        try:
            size = path.stat().st_size
            path.unlink()
            return RemovalResult(success=True, bytes_freed=size, log="Deleted")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        return False

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
