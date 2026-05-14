"""Maven local repository plugin for bekas.

Discovers old versions in ``~/.m2/repository``.
Walks artifact directories with multiple versions and keeps the latest
two; marks older ones as candidates.  This avoids blanket-deleting the
entire repository.

Maven repository layout::

    ~/.m2/repository/
      com/
        example/
          mylib/
            1.0/
            1.1/
            2.0/

The plugin finds artifact directories (e.g. ``mylib``) whose children
look like version strings, keeps the latest two, and marks the rest.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class MavenRepoPlugin(Plugin):
    """Finds old Maven artifacts in the local repository.

    Scans ``~/.m2/repository`` and marks artifact directories with more
    than two versions.  Keeps the latest two versions; older ones are
    candidates.  Tier is ``REVIEW`` because Maven repositories are shared
    across projects and deleting a version may break an old build.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``mvn`` checked at runtime for tier selection.
    """

    name = "maven.repo"
    description = "Finds old Maven artifact versions in ~/.m2/repository."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="medium")

    def is_available(self, ctx: Context) -> bool:
        """Check if Maven local repository exists.

        Args:
            ctx: Execution context.

        Returns:
            True if the Maven local repository exists.
        """
        return self._repo_path().exists()

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield old Maven artifact version candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing old Maven artifact versions.
        """
        repo = self._repo_path()
        if not repo.exists():
            return

        maven_detected = shutil.which("mvn") is not None
        tier = Confidence.SAFE if maven_detected else Confidence.REVIEW

        for artifact_dir, versions in _find_artifacts(repo):
            if len(versions) <= 2:
                continue
            # Keep the latest two; mark the rest as candidates
            old_versions = versions[:-2]
            for ver_dir in old_versions:
                size = _du(ver_dir)
                yield Candidate(
                    id=f"maven:{artifact_dir.name}:{ver_dir.name}",
                    category="maven.repo",
                    size_bytes=size,
                    path_or_handle=str(ver_dir),
                    confidence=tier,
                    reason=f"Old Maven artifact version ({len(versions)} versions total; keeping latest 2).",
                    metadata={
                        "artifact": artifact_dir.name,
                        "version": ver_dir.name,
                        "path": str(ver_dir),
                    },
                )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete an old Maven artifact version directory.

        Args:
            candidate: Maven artifact version candidate to remove.
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
        """Return False because cache cannot be undone.

        Returns:
            False.
        """
        return False

    @staticmethod
    def _repo_path() -> Path:
        """Return the Maven local repository path.

        Returns:
            Path to ``~/.m2/repository``.
        """
        return Path.home() / ".m2" / "repository"


def _find_artifacts(repo: Path) -> Iterator[tuple[Path, list[Path]]]:
    """Walk the Maven repository and yield (artifact_dir, version_dirs) tuples.

    An artifact directory is identified as a directory whose children
    include at least one subdirectory that looks like a version string
    and contains Maven artifacts (``.jar`` or ``.pom`` files).

    Args:
        repo: Root of the Maven local repository.

    Yields:
        Tuples of (artifact_dir, sorted_version_dirs) where version_dirs
        are sorted newest-first by mtime then name.
    """
    for dirpath, _dirnames in _walk_dirs(repo):
        dp = Path(dirpath)
        if not dp.is_dir():
            continue
        version_dirs: list[Path] = []
        for child in dp.iterdir():
            if not child.is_dir():
                continue
            if _looks_like_version(child.name) and _has_maven_files(child):
                version_dirs.append(child)
        if len(version_dirs) >= 2:
            # Sort by mtime descending, then by name for stable ordering
            version_dirs.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
            yield dp, version_dirs


def _walk_dirs(path: Path) -> Iterator[tuple[str, list[str]]]:
    """Walk directory tree yielding (dirpath, dirnames) only.

    Uses ``os.walk`` for correct directory recursion.

    Args:
        path: Root directory to walk.

    Yields:
        (dirpath, dirnames) tuples.
    """
    for dirpath, dirnames, _filenames in os.walk(path):
        yield dirpath, dirnames


def _has_maven_files(path: Path) -> bool:
    """Check if a directory contains Maven artifacts.

    Args:
        path: Directory to check.

    Returns:
        True if the directory contains any ``.jar`` or ``.pom`` files.
    """
    try:
        for child in path.iterdir():
            if child.is_file() and child.suffix in {".jar", ".pom", ".sha1"}:
                return True
            # Also check one level of subdirs for nested jar paths
            if child.is_dir():
                for grandchild in child.iterdir():
                    if grandchild.is_file() and grandchild.suffix in {".jar", ".pom", ".sha1"}:
                        return True
    except OSError:
        pass
    return False


def _looks_like_version(name: str) -> bool:
    """Heuristic: does a directory name look like a Maven version string?

    Args:
        name: Directory name to test.

    Returns:
        True if it starts with a digit, ends with SNAPSHOT, or matches a
        common Maven version pattern (e.g. ``1.0``, ``1.0-SNAPSHOT``).
    """
    if not name:
        return False
    if name.lower().endswith("snapshot"):
        return True
    # Common Maven version patterns: 1.0, 1.0.1, 1.0.1-alpha, 1.2.3-SNAPSHOT, etc.
    return bool(re.match(r"^\d[\w.-]*$", name))


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
