"""Chrome browser cache plugin for bekas.

Discovers Chrome cache directories across platforms.  Only touches
``Cache/``, ``Code Cache/``, ``GPUCache/``, and ``Service Worker/CacheStorage/``
subdirectories — never profile directories themselves.

Default tier ``REVIEW`` because users notice when their browser feels "fresh".
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class BrowserChromePlugin(Plugin):
    """Finds Chrome and Chromium browser caches.

    Scans ``~/.config/google-chrome/*`` and ``~/.config/chromium/*``
    (plus macOS/Windows equivalents).  Only targets cache subdirectories,
    never profiles.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
    """

    name = "browser.chrome"
    description = "Finds Chrome browser caches (Cache, Code Cache, GPUCache, Service Worker)."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if Chrome cache directories exist.

        Args:
            ctx: Execution context.

        Returns:
            True if any Chrome cache directory exists.
        """
        return bool(_chrome_cache_dirs())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Chrome cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing Chrome cache directories.
        """
        cache_dirs = _chrome_cache_dirs()
        if not cache_dirs:
            return

        for cache_dir in cache_dirs:
            size = _du(cache_dir)
            if size == 0:
                continue
            yield Candidate(
                id=f"chrome:{cache_dir.name}",
                category="browser.chrome",
                size_bytes=size,
                path_or_handle=str(cache_dir),
                confidence=Confidence.REVIEW,
                reason="Chrome browser cache (regenerates automatically).",
                metadata={"path": str(cache_dir)},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a Chrome cache directory.

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


def _chrome_profiles() -> list[Path]:
    """Return Chrome profile directory paths.

    Returns:
        List of profile directories.
    """
    system = os.name
    home = Path.home()
    profiles: list[Path] = []

    if system == "posix":
        # Linux — Chrome + Chromium
        for chrome_name in ("google-chrome", "chromium"):
            linux_base = home / ".config" / chrome_name
            if linux_base.exists():
                profiles.extend(p for p in linux_base.iterdir() if p.is_dir())
        # macOS
        for chrome_name in ("Google/Chrome", "Chromium"):
            mac_base = home / "Library" / "Application Support" / chrome_name
            if mac_base.exists():
                profiles.extend(p for p in mac_base.iterdir() if p.is_dir())
    elif system == "nt":
        # Windows
        for chrome_name in ("Chrome", "Chromium"):
            win_base = home / "AppData" / "Local" / "Google" / chrome_name / "User Data"
            if win_base.exists():
                profiles.extend(p for p in win_base.iterdir() if p.is_dir())
            win_base2 = home / "AppData" / "Local" / chrome_name / "User Data"
            if win_base2.exists():
                profiles.extend(p for p in win_base2.iterdir() if p.is_dir())

    return profiles


def _chrome_cache_dirs() -> list[Path]:
    """Return Chrome cache subdirectory paths.

    Only returns cache-related subdirectories inside profiles:
    Cache, Code Cache, GPUCache, Service Worker/CacheStorage.

    Returns:
        List of cache directory paths.
    """
    cache_names = {"Cache", "Code Cache", "GPUCache"}
    dirs: list[Path] = []
    for profile in _chrome_profiles():
        for name in cache_names:
            cp = profile / name
            if cp.exists():
                dirs.append(cp)
        # Service Worker cache storage
        sw_cache = profile / "Service Worker" / "CacheStorage"
        if sw_cache.exists():
            dirs.append(sw_cache)
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
