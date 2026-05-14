"""JetBrains caches plugin for bekas.

Discovers JetBrains IDE caches for versions older than the latest
installed.  Tier ``SAFE``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class JetbrainsCachesPlugin(Plugin):
    """Finds old JetBrains IDE caches.

    Scans ``~/Library/Caches/JetBrains/*`` and ``~/.cache/JetBrains/*``.
    Only marks caches for IDE versions older than the latest installed.
    Tier ``SAFE`` because caches are fully reproducible.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required.
    """

    name = "jetbrains.caches"
    description = "Finds old JetBrains IDE caches (older than latest installed version)."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if JetBrains cache directories exist.

        Args:
            ctx: Execution context.

        Returns:
            True if any JetBrains cache directory exists.
        """
        return bool(_jetbrains_cache_dirs())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield old JetBrains cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing old JetBrains IDE caches.
        """
        cache_dirs = _jetbrains_cache_dirs()
        if not cache_dirs:
            return

        latest = _latest_jetbrains_version()

        for cache_dir in cache_dirs:
            version = _extract_version(cache_dir.name)
            # If we can't parse a version, or it's the latest, skip
            if version is None:
                continue
            if latest and version == latest:
                continue

            size = _du(cache_dir)
            if size == 0:
                continue

            yield Candidate(
                id=f"jetbrains:{cache_dir.name}",
                category="jetbrains.caches",
                size_bytes=size,
                path_or_handle=str(cache_dir),
                confidence=Confidence.SAFE,
                reason=f"JetBrains cache for older version ({version}; latest={latest}).",
                metadata={"version": version, "latest": latest, "path": str(cache_dir)},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a JetBrains cache directory.

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
        """Return False because caches cannot be undone.

        Returns:
            False.
        """
        return False


def _jetbrains_cache_dirs() -> list[Path]:
    """Return JetBrains cache directory paths.

    Returns:
        List of existing JetBrains cache directories.
    """
    home = Path.home()
    candidates = [
        home / "Library" / "Caches" / "JetBrains",
        home / ".cache" / "JetBrains",
    ]
    dirs: list[Path] = []
    for base in candidates:
        if base.exists():
            dirs.extend(p for p in base.iterdir() if p.is_dir())
    return dirs


def _latest_jetbrains_version() -> str | None:
    """Determine the latest installed JetBrains IDE version.

    Parses directory names like ``IntelliJIdea2024.1`` to extract the
    version string.  Returns the lexicographically largest version found.

    Returns:
        Latest version string, or None if no version could be parsed.
    """
    versions: list[str] = []
    for cache_dir in _jetbrains_cache_dirs():
        v = _extract_version(cache_dir.name)
        if v:
            versions.append(v)
    if not versions:
        return None
    # Sort descending and pick the first
    versions.sort(reverse=True)
    return versions[0]


def _extract_version(name: str) -> str | None:
    """Extract a version string from a JetBrains directory name.

    Examples:
        ``IntelliJIdea2024.1`` → ``2024.1``
        ``PyCharm2023.3`` → ``2023.3``

    Args:
        name: Directory name.

    Returns:
        Version string, or None if no version pattern is found.
    """
    import re

    m = re.search(r"(\d{4}\.\d+)", name)
    if m:
        return m.group(1)
    return None


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
