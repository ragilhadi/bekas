"""Devtool caches plugin for bekas.

Discovers tool-local cache directories (``.pytest_cache``, ``.mypy_cache``,
``.ruff_cache``) inside common project roots.  These caches are fully
regenerable by the underlying tools, so every candidate is ``SAFE``.
There is no quarantine and no undo — the caches rebuild on next run.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin

_PRUNED_DIRS: frozenset[str] = frozenset({"node_modules", ".venv", "venv", ".git"})

_CACHE_DIR_NAMES: dict[str, str] = {
    ".pytest_cache": "pytest.cache",
    ".mypy_cache": "mypy.cache",
    ".ruff_cache": "ruff.cache",
}

_MAX_DEPTH = 6


class DevtoolCachesPlugin(Plugin):
    """Finds tool-local caches (pytest, mypy, ruff) under project roots.

    Walks ``~/code``, ``~/projects``, ``~/dev`` and ``$HOME`` up to depth 6,
    pruning dependency and VCS directories.  All candidates are ``SAFE``
    because the caches are regenerated automatically.

    Attributes:
        name: Plugin identifier.
        categories: Candidate categories emitted by this plugin.
        requires_commands: No external commands required.
    """

    name = "devtool.caches"
    categories = ("pytest.cache", "mypy.cache", "ruff.cache")
    description = "Finds pytest, mypy and ruff cache directories in projects."
    requires_commands: list[str] = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check whether any known devtool cache directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if at least one cache directory was found.
        """
        return next(self._find_caches(), None) is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield devtool cache candidates under the project roots.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects, one per cache directory found.
        """
        for cache_dir, category in self._find_caches():
            size = _du(cache_dir)
            tool = category.split(".")[0]
            yield Candidate(
                id=f"devtool:{cache_dir}",
                category=category,
                size_bytes=size,
                path_or_handle=str(cache_dir),
                confidence=Confidence.SAFE,
                reason=f"{tool.capitalize()} cache is regenerable; the tool rebuilds it on next run.",
                metadata={"path": str(cache_dir), "tool": tool},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the exact cache directory of a candidate.

        Args:
            candidate: Cache candidate to remove.
            ctx: Execution context; on ``dry_run`` nothing is deleted.

        Returns:
            Result of the removal attempt.
        """
        path = Path(candidate.path_or_handle)
        cache_name = _CACHE_DIR_NAMES.get(path.name)
        if cache_name is None or cache_name not in self.categories:
            return RemovalResult(success=False, bytes_freed=0, log=f"Not a devtool cache directory: {path.name}")
        if not path.is_dir():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        if ctx.dry_run:
            return RemovalResult(success=False, bytes_freed=0, log="Dry run: would delete cache directory")
        try:
            size = _du(path)
            shutil.rmtree(path)
            return RemovalResult(success=True, bytes_freed=size, log="Deleted devtool cache directory")
        except OSError as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return False because caches are regenerable, not restorable.

        Returns:
            Whether undo is supported.
        """
        return False

    def _find_caches(self) -> Iterator[tuple[Path, str]]:
        """Yield (cache_dir, category) pairs found under the search roots.

        Yields:
            Tuples of cache directory and its category name.
        """
        roots = [
            Path.home() / "code",
            Path.home() / "projects",
            Path.home() / "dev",
            Path(os.environ.get("HOME", str(Path.home()))),
        ]
        seen_roots: set[Path] = set()
        seen_caches: set[Path] = set()
        for root in roots:
            if not root.is_dir():
                continue
            resolved = root.resolve()
            if resolved in seen_roots:
                continue
            seen_roots.add(resolved)
            for cache_dir in _walk_for_caches(root, 0):
                resolved_cache = cache_dir.resolve()
                if resolved_cache in seen_caches:
                    continue
                seen_caches.add(resolved_cache)
                yield cache_dir, _CACHE_DIR_NAMES[cache_dir.name]


def _walk_for_caches(root: Path, depth: int) -> Iterator[Path]:
    """Recursively scan a directory tree for devtool cache directories.

    Args:
        root: Directory to scan.
        depth: Current recursion depth (capped at ``_MAX_DEPTH``).

    Yields:
        Existing cache directory paths.
    """
    if depth > _MAX_DEPTH:
        return
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if name in _CACHE_DIR_NAMES:
            yield entry
        elif name not in _PRUNED_DIRS and not name.startswith("."):
            yield from _walk_for_caches(entry, depth + 1)


def _du(path: Path) -> int:
    """Compute total byte size of a directory tree.

    Args:
        path: Directory to measure.

    Returns:
        Sum of file sizes in bytes.
    """
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file() and not f.is_symlink():
                with contextlib.suppress(OSError):
                    total += f.stat().st_size
    except OSError:
        pass
    return total
