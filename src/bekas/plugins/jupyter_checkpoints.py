"""Jupyter checkpoints plugin for bekas.

Discovers Jupyter notebook checkpoint directories (``.ipynb_checkpoints``)
by walking the user's home directory to a bounded depth. Checkpoints are
stale auto-saves of the notebook itself, so they are SAFE to remove —
Jupyter recreates them when the notebook is next saved.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin

CHECKPOINT_DIRNAME = ".ipynb_checkpoints"
MAX_DEPTH = 8

# Directory names pruned from the walk: dependency trees and virtual
# environments never contain notebook checkpoints and are far too large
# and deep to traverse.
PRUNED_DIRS = {"node_modules", "venv", ".venv", "env", ".env", "virtualenv", "conda-meta"}


class JupyterCheckpointsPlugin(Plugin):
    """Finds Jupyter notebook checkpoint directories.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        capabilities: No quarantine (checkpoints are trivially recreatable).
    """

    name = "jupyter.checkpoints"
    description = "Finds Jupyter .ipynb_checkpoints directories."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if any checkpoint directory exists under the home directory.

        Args:
            ctx: Execution context.

        Returns:
            True if at least one ``.ipynb_checkpoints`` directory exists.
        """
        return next(_find_checkpoints(Path.home()), None) is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield one SAFE candidate per checkpoint directory.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing ``.ipynb_checkpoints`` directories.
        """
        for checkpoint in _find_checkpoints(Path.home()):
            size = _du(checkpoint)
            yield Candidate(
                id=f"jupyter.checkpoints:{checkpoint}",
                category="jupyter.checkpoints",
                size_bytes=size,
                path_or_handle=str(checkpoint),
                confidence=Confidence.SAFE,
                reason="Jupyter notebook checkpoint (recreated automatically on next save).",
                metadata={"path": str(checkpoint)},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a checkpoint directory.

        Args:
            candidate: Checkpoint candidate to remove.
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
        """Return False because checkpoints cannot be undone.

        Returns:
            False.
        """
        return False


def _find_checkpoints(root: Path) -> Iterator[Path]:
    """Walk a directory tree to a bounded depth yielding checkpoint dirs.

    Args:
        root: Directory to walk (usually the user's home).

    Yields:
        Paths to ``.ipynb_checkpoints`` directories, deepest-first not
        guaranteed; each existing directory is yielded at most once.
    """
    for dirpath, dirnames, _ in os.walk(root):
        current = Path(dirpath)
        depth = len(current.relative_to(root).parts)
        if depth >= MAX_DEPTH:
            dirnames[:] = []
            continue
        if current.name == CHECKPOINT_DIRNAME and current.exists():
            dirnames[:] = []
            continue
        for d in list(dirnames):
            if d == CHECKPOINT_DIRNAME:
                checkpoint = current / d
                if checkpoint.is_dir():
                    yield checkpoint
                dirnames.remove(d)
            elif d in PRUNED_DIRS:
                dirnames.remove(d)


def _du(path: Path) -> int:
    """Compute the total byte size of a path recursively.

    Args:
        path: File or directory to measure.

    Returns:
        Total size in bytes.
    """
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
