"""Dotfiles backup files plugin for bekas."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class DotfilesBackupsPlugin(Plugin):
    """Finds old dotfile backup files.

    Scans the home directory for backup files matching common patterns
    such as ``.zshrc.backup-*`` or ``.bashrc.bak``.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required.
        PATTERNS: Glob patterns used to identify backup files.
    """

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
        """Yield backup file candidates from the home directory.

        Args:
            ctx: Execution context.

        Yields:
            Candidate objects representing backup files.
        """
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
        """Delete a backup file.

        Args:
            candidate: Backup candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
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
        """Return False because undo is not supported for backup files.

        Returns:
            Whether undo is supported.
        """
        return False

    def supports_quarantine(self) -> bool:
        """Return True because backup files can be quarantined.

        Returns:
            Whether quarantine is supported.
        """
        return True

    def quarantine(
        self,
        candidate: Candidate,
        ctx: Context,
        quarantine_dir: str,
        run_id: str | None = None,
    ) -> RemovalResult:
        """Quarantine a backup file.

        Args:
            candidate: Backup candidate to quarantine.
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
