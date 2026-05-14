"""Safari browser cache plugin for bekas (macOS only).

Discovers the Safari cache directory on macOS.
Only touches ``~/Library/Caches/com.apple.Safari``.

Default tier ``REVIEW`` because users notice when their browser feels "fresh".
"""

from __future__ import annotations

import platform
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class BrowserSafariPlugin(Plugin):
    """Finds Safari browser caches on macOS.

    Scans ``~/Library/Caches/com.apple.Safari``.
    Only targets the cache directory, never bookmarks/history.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
    """

    name = "browser.safari"
    description = "Finds Safari browser caches (macOS only)."
    requires_commands = []
    capabilities = Capabilities(
        quarantine=False,
        estimated_runtime="fast",
        platforms=("darwin",),
    )

    def is_available(self, ctx: Context) -> bool:
        """Check if Safari cache directory exists (macOS only).

        Args:
            ctx: Execution context.

        Returns:
            True if on macOS and the Safari cache directory exists.
        """
        if platform.system().lower() != "darwin":
            return False
        return _safari_cache_dir().exists()

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Safari cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing the Safari cache.
        """
        cache_dir = _safari_cache_dir()
        if not cache_dir.exists():
            return

        size = _du(cache_dir)
        if size == 0:
            return

        yield Candidate(
            id="safari:cache",
            category="browser.safari",
            size_bytes=size,
            path_or_handle=str(cache_dir),
            confidence=Confidence.REVIEW,
            reason="Safari browser cache (regenerates automatically).",
            metadata={"path": str(cache_dir)},
        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the Safari cache directory.

        Args:
            candidate: Cache candidate to remove.
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
        """Return False because browser cache cannot be undone.

        Returns:
            False.
        """
        return False


def _safari_cache_dir() -> Path:
    """Return the Safari cache directory path.

    Returns:
        Path to ``~/Library/Caches/com.apple.Safari``.
    """
    return Path.home() / "Library" / "Caches" / "com.apple.Safari"


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
