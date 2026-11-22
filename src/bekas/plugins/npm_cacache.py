"""npm cache plugin for bekas.

Discovers the npm content-addressable cache (``~/.npm/_cacache``) and the
npm log directory (``~/.npm/_logs``).  Both are fully regenerable — npm
redownloads packages and recreates logs on demand — so all candidates are
SAFE.  There is no quarantine.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class NpmCacachePlugin(Plugin):
    """Finds and optionally removes the npm cacache and logs.

    Scans ``~/.npm/_cacache`` (content-addressable package cache) and
    ``~/.npm/_logs`` (debug logs).  npm fully regenerates both, so items
    are ``SAFE``.  There is no quarantine.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required (pure filesystem).
    """

    name = "npm.cacache"
    description = "Finds the npm package cache and logs."
    requires_commands: list[str] = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    _NPM_DIRS: tuple[tuple[str, str, str, str], ...] = (
        ("_cacache", "npm:cacache", "npm.cacache", "npm content-addressable cache (redownloaded on demand)."),
        ("_logs", "npm:logs", "npm.cacache", "npm debug logs (regenerated on demand)."),
    )

    def is_available(self, ctx: Context) -> bool:
        """Check if any npm cache subdirectory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if ``~/.npm/_cacache`` or ``~/.npm/_logs`` exists.
        """
        return bool(self._cache_paths())

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield npm cacache and log candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects, one per existing npm cache subdirectory.
        """
        npm_root = Path.home() / ".npm"
        if not npm_root.exists():
            return
        for subdir, cid, category, reason in self._NPM_DIRS:
            p = npm_root / subdir
            if not p.exists():
                continue
            size = _du(p)
            if size == 0:
                continue
            yield Candidate(
                id=cid,
                category=category,
                size_bytes=size,
                path_or_handle=str(p),
                confidence=Confidence.SAFE,
                reason=reason,
                metadata={"path": str(p)},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete the candidate's npm cache directory.

        Args:
            candidate: Cache candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.  In dry-run mode nothing is
            modified and ``log="dry-run"`` is returned.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, log="dry-run")
        p = Path(candidate.path_or_handle)
        # Only delete the exact discovered path, never the enclosing ~/.npm.
        if p.name not in ("_cacache", "_logs") or p.parent.name != ".npm":
            return RemovalResult(success=False, bytes_freed=0, log="Refusing to delete unexpected path")
        if not p.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            freed = _du(p)
            shutil.rmtree(p)
            return RemovalResult(success=True, bytes_freed=freed, undo_token=None, log=f"Deleted {p}")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return False because the cache cannot be undone.

        Returns:
            False.
        """
        return False

    @staticmethod
    def _cache_paths() -> list[Path]:
        """Return discovered npm cache subdirectory paths.

        Returns:
            List of existing ``~/.npm`` cache subdirectories.
        """
        npm_root = Path.home() / ".npm"
        return [npm_root / name for name, _, _, _ in NpmCacachePlugin._NPM_DIRS if (npm_root / name).exists()]


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
