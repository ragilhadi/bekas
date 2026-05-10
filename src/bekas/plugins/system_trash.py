"""System Trash / Recycle Bin plugin for bekas.

Discovers and cleans the user's Trash across macOS and Linux (XDG).
Items are already in a soft-delete location, so all candidates are SAFE.

On Linux, deleting from Trash also removes the matching ``.trashinfo``
file in the ``info/`` subdirectory.
"""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class SystemTrashPlugin(Plugin):
    """Finds and removes items from the system Trash.

    Scans platform-specific trash directories.  Items are already in a
    soft-delete location, so all candidates are ``SAFE``.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
    """

    name = "system.trash"
    description = "Finds items in the Trash / Recycle Bin."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield trash item candidates.

        Args:
            ctx: Execution context (unused).

        Yields:
            Candidate objects representing trash items.
        """
        for trash_dir in _trash_paths():
            if not trash_dir.exists():
                continue
            for entry in trash_dir.iterdir():
                size = _du(entry) if entry.is_dir() else _file_size(entry)
                if size <= 0:
                    size = 0
                yield Candidate(
                    id=f"trash:{entry.name}",
                    category="system.trash",
                    size_bytes=size,
                    path_or_handle=str(entry),
                    confidence=Confidence.SAFE,
                    reason="Item is already in Trash (soft-delete).",
                    metadata={"path": str(entry), "is_dir": entry.is_dir()},
                )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a trash item and any associated trashinfo file.

        On Linux (XDG), also removes the ``.trashinfo`` file in ``info/``.

        Args:
            candidate: Trash candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")

        try:
            size = _du(path) if path.is_dir() else _file_size(path)
            _delete_with_trashinfo(path)
            return RemovalResult(success=True, bytes_freed=size, log="Deleted from trash")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return False — undo not supported.

        Returns:
            False.
        """
        return False


def _trash_paths() -> list[Path]:
    """Return platform-specific trash directory paths.

    Returns:
        List of Paths to trash item directories.
    """
    system = platform.system().lower()
    home = Path.home()
    if system == "darwin":
        return [home / ".Trash"]
    # Linux / XDG and default
    return [home / ".local" / "share" / "Trash" / "files"]


def _file_size(path: Path) -> int:
    """Get the size of a file, returning 0 on error.

    Args:
        path: File path.

    Returns:
        File size in bytes.
    """
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _du(path: Path) -> int:
    """Compute total byte size of a directory recursively.

    Args:
        path: Directory path.

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


def _delete_with_trashinfo(path: Path) -> None:
    """Delete a trash item and its corresponding ``.trashinfo`` file.

    On Linux XDG Trash, each item in ``files/`` may have a matching
    ``<name>.trashinfo`` in ``info/``.  This helper removes both.

    Args:
        path: Path to the trash item (probably under ``files/``).
    """
    # On Linux, try to locate and delete the .trashinfo file
    system = platform.system().lower()
    if system == "linux":
        parts = list(path.parts)
        # Find the 'files' segment and replace with 'info'
        if "files" in parts:
            idx = parts.index("files")
            info_parts = list(parts)
            info_parts[idx] = "info"
            trashinfo = Path(*info_parts).with_suffix(path.suffix + ".trashinfo")
            if trashinfo.exists():
                try:
                    trashinfo.unlink()
                except OSError:
                    pass
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
