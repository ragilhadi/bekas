"""Python cache directories plugin for bekas."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class PythonCachePlugin(Plugin):
    """Finds __pycache__ and tool caches in old/abandoned projects."""

    name = "python.cache"
    description = "Finds __pycache__, .pytest_cache, .mypy_cache, .ruff_cache in old projects."
    requires_commands = []

    CACHE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        roots = [Path.home() / "code", Path.home() / "projects", Path.home() / "dev", Path.home()]
        roots = [r for r in roots if r.exists()]
        min_idle_days = ctx.config.get("plugin_settings", {}).get("python.cache", {}).get("min_idle_days", 90)
        cutoff = datetime.now() - timedelta(days=min_idle_days)
        seen: set[Path] = set()

        for root in roots:
            for cache_path in _find_caches(root, seen):
                parent = cache_path.parent
                markers = ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"]
                has_project = any((parent / m).exists() for m in markers)
                size = _du(cache_path)

                if not has_project:
                    yield Candidate(
                        id=f"pycache:{cache_path}",
                        category="python.cache",
                        size_bytes=size,
                        path_or_handle=str(cache_path),
                        confidence=Confidence.SAFE,
                        reason="Cache directory in a folder with no Python project markers.",
                        metadata={"path": str(cache_path)},
                    )
                else:
                    mtimes = [(parent / m).stat().st_mtime for m in markers if (parent / m).exists()]
                    if mtimes and datetime.fromtimestamp(max(mtimes)) < cutoff:
                        yield Candidate(
                            id=f"pycache:{cache_path}",
                            category="python.cache",
                            size_bytes=size,
                            path_or_handle=str(cache_path),
                            confidence=Confidence.SAFE,
                            reason="Cache directory in a project untouched for 90+ days. Safe to regenerate.",
                            metadata={"path": str(cache_path)},
                        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size = _du(path)
            import shutil

            shutil.rmtree(path)
            return RemovalResult(success=True, bytes_freed=size, log="Deleted")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))


def _find_caches(root: Path, seen: set[Path]) -> Iterator[Path]:
    for dirpath, dirnames, _ in os.walk(root):
        dp = Path(dirpath)
        for d in list(dirnames):
            if d in PythonCachePlugin.CACHE_NAMES:
                cp = dp / d
                try:
                    real = cp.resolve()
                except OSError:
                    real = cp
                if real not in seen:
                    seen.add(real)
                    yield real
                dirnames.remove(d)


def _du(path: Path) -> int:
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total
