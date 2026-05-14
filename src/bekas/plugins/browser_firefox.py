"""Firefox browser cache plugin for bekas.

Discovers Firefox cache directories across platforms.  Only touches
``cache2`` subdirectories inside profile directories — never profile
data itself.

Default tier ``REVIEW`` because users notice when their browser feels "fresh".
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class BrowserFirefoxPlugin(Plugin):
    """Finds Firefox browser caches.

    Scans ``~/.mozilla/firefox/*/cache2`` and
    ``~/Library/Application Support/Firefox/Profiles/*/cache2``.
    Only targets cache subdirectories, never profiles.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
    """

    name = "browser.firefox"
    description = "Finds Firefox browser caches (cache2)."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if Firefox cache directories exist.

        Args:
            ctx: Execution context.

        Returns:
            True if any Firefox cache directory exists.
        """
        return bool(_firefox_cache_dirs())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Firefox cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing Firefox cache directories.
        """
        cache_dirs = _firefox_cache_dirs()
        if not cache_dirs:
            return

        for cache_dir in cache_dirs:
            size = _du(cache_dir)
            if size == 0:
                continue
            yield Candidate(
                id=f"firefox:{cache_dir.name}",
                category="browser.firefox",
                size_bytes=size,
                path_or_handle=str(cache_dir),
                confidence=Confidence.REVIEW,
                reason="Firefox browser cache (regenerates automatically).",
                metadata={"path": str(cache_dir)},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a Firefox cache directory.

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


def _firefox_profiles() -> list[Path]:
    """Return Firefox profile directory paths.

    Returns:
        List of profile directories.
    """
    system = os.name
    home = Path.home()
    profiles: list[Path] = []

    if system == "posix":
        # Linux — profiles live in ~/.mozilla/firefox/
        linux_base = home / ".mozilla" / "firefox"
        if linux_base.exists():
            profiles.extend(p for p in linux_base.iterdir() if p.is_dir())
        # macOS — profiles live in ~/Library/Application Support/Firefox/Profiles/
        mac_base = home / "Library" / "Application Support" / "Firefox" / "Profiles"
        if mac_base.exists():
            profiles.extend(p for p in mac_base.iterdir() if p.is_dir())
    elif system == "nt":
        # Windows — profiles live in %APPDATA%\Mozilla\Firefox\Profiles
        win_base = home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles"
        if win_base.exists():
            profiles.extend(p for p in win_base.iterdir() if p.is_dir())

    return profiles


def _firefox_cache_dirs() -> list[Path]:
    """Return Firefox cache subdirectory paths.

    Returns:
        List of cache2 directory paths.
    """
    dirs: list[Path] = []
    for profile in _firefox_profiles():
        cache2 = profile / "cache2"
        if cache2.exists():
            dirs.append(cache2)
    return dirs


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
