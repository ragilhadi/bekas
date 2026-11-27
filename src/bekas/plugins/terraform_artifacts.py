"""Terraform artifacts plugin for bekas.

Discovers ``.terraform`` working directories inside Terraform projects
(marked by ``*.tf`` files or a ``terraform.lock.hcl`` lockfile).

``.terraform`` holds downloaded providers/plugins and installed modules: all
of it is re-fetchable, but the surrounding project should decide, so
candidates are ``REVIEW``. ``remove()`` deletes only regenerable content
inside ``.terraform`` (``providers/``, ``modules/``, ``*.lock.json``);
``terraform.lock.hcl`` lives in the project root and is never deleted.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.fsmeasure import measure_path
from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin

_MAX_DEPTH = 5
_MAX_TREE_ENTRIES = 20_000


class TerraformArtifactsPlugin(Plugin):
    """Finds `.terraform` working directories in Terraform projects.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required.
    """

    name = "terraform.artifacts"
    categories = ("terraform.artifacts",)
    description = "Finds .terraform provider/module caches inside Terraform projects."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="medium")

    def is_available(self, ctx: Context) -> bool:
        """Check whether any Terraform project artifacts exist under home.

        Args:
            ctx: Execution context.

        Returns:
            True if at least one `.terraform` dir in a Terraform project exists.
        """
        return next(self._terraform_dirs(ctx), None) is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield `.terraform` directory candidates.

        Args:
            ctx: Execution context.

        Yields:
            Candidate objects representing `.terraform` directories.
        """
        for tf_dir in self._terraform_dirs(ctx):
            try:
                size_bytes, mtime = measure_path(tf_dir)
            except OSError:
                size_bytes, mtime = 0, None
            lock_hcl = tf_dir.parent / "terraform.lock.hcl"
            yield Candidate(
                id=f"terraform:{tf_dir}",
                category="terraform.artifacts",
                size_bytes=size_bytes,
                path_or_handle=str(tf_dir),
                confidence=Confidence.REVIEW,
                reason=(
                    "Terraform working directory (downloaded providers and installed "
                    f"modules); {lock_hcl.name} in the project root is never deleted."
                ),
                mtime=mtime,
                metadata={"path": str(tf_dir), "project": str(tf_dir.parent), "lockfile": str(lock_hcl)},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete regenerable content inside one `.terraform` dir.

        Deletion is an explicit allowlist of paths *inside* the working
        directory, so the project's ``terraform.lock.hcl`` (which lives in
        the project root) is never deleted; providers re-resolve through it
        with ``terraform providers lock`` / ``init``.

        Args:
            candidate: Candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the removal attempt.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, undo_token=None, log="dry-run")
        tf_dir = Path(candidate.path_or_handle)
        if not tf_dir.is_dir() or tf_dir.name != ".terraform":
            return RemovalResult(success=False, bytes_freed=0, log=f"Not a .terraform directory: {tf_dir}")
        deleted, freed = _delete_regenerable_artifacts(tf_dir)
        return RemovalResult(success=deleted, bytes_freed=freed, undo_token=None, log="Deleted terraform artifacts")

    def supports_undo(self) -> bool:
        """Return False because provider caches are re-downloadable, not undoable.

        Returns:
            Whether undo is supported.
        """
        return False

    def _terraform_dirs(self, ctx: Context) -> Iterator[Path]:
        """Walk bounded roots under home and yield `.terraform` directories.

        Roots are the home directory itself and well-known code roots
        (``~/code``, ``~/projects``, ``~/dev``, ``~/src``, ``~/repos``,
        ``~/work``). The walk is depth-capped and skips heavy non-project
        trees; a directory only counts when its parent looks like a
        Terraform project (``*.tf`` files or ``terraform.lock.hcl``).

        Args:
            ctx: Execution context (unused; interface symmetry).

        Yields:
            Paths of `.terraform` directories belonging to Terraform projects.
        """
        _ = ctx
        home = Path.home()
        roots = [home]
        for base in ("code", "projects", "dev", "src", "repos", "work"):
            candidate_root = home / base
            if candidate_root.is_dir():
                roots.append(candidate_root)
        seen: set[Path] = set()
        for root in roots:
            for dirpath, dirnames, _ in os.walk(root, topdown=True):
                depth = len(Path(dirpath).relative_to(root).parts)
                if depth >= _MAX_DEPTH:
                    dirnames.clear()
                    continue
                # Keep the walk cheap: never descend into heavy non-project
                # trees. ".terraform" itself stays visible so the loop below
                # can see and yield it; it is then removed from dirnames.
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS or d == ".terraform"]
                for name in list(dirnames):
                    if name != ".terraform":
                        continue
                    tf_dir = Path(dirpath) / name
                    project = tf_dir.parent
                    if _is_terraform_project(project) and project not in seen:
                        seen.add(project)
                        dirnames.remove(name)  # never descend into .terraform itself
                        yield tf_dir
                    elif not _is_terraform_project(project):
                        dirnames.remove(name)  # stray .terraform without a project: ignore entirely


_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".terraform"}


def _is_terraform_project(directory: Path) -> bool:
    """Check whether a directory looks like a Terraform project.

    Args:
        directory: Candidate project directory.

    Returns:
        True if it contains *.tf files or a terraform.lock.hcl.
    """
    try:
        if (directory / "terraform.lock.hcl").is_file():
            return True
        return any(directory.glob("*.tf"))
    except OSError:
        return False


def _delete_regenerable_artifacts(tf_dir: Path) -> tuple[bool, int]:
    """Delete regenerable content inside a `.terraform` directory.

    Removes ``providers/``, ``modules/``, and ``*.lock.json`` lock-info
    files, leaving ``terraform.tf`` state files and anything else untouched.

    Args:
        tf_dir: The `.terraform` directory to clean.

    Returns:
        ``(all_deleted, bytes_freed)``; ``all_deleted`` is True only when at
        least one target existed and every delete succeeded.
    """
    freed = 0
    failed = False
    targets: list[Path] = []
    providers = tf_dir / "providers"
    modules = tf_dir / "modules"
    if providers.is_dir():
        targets.append(providers)
    if modules.is_dir():
        targets.append(modules)
    for lock_info in sorted(tf_dir.glob("*.lock.json")):
        if lock_info.is_file():
            targets.append(lock_info)
    if not targets:
        return False, 0
    for target in targets:
        try:
            freed += _du(target)
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError:
            failed = True
    return not failed, freed


def _du(path: Path) -> int:
    """Compute the total byte size of a path recursively.

    Args:
        path: File or directory to measure.

    Returns:
        Total size in bytes (bounded for pathological trees).
    """
    total = 0
    visited = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            if current.is_file():
                total += current.stat().st_size
                continue
            for child in current.iterdir():
                visited += 1
                if visited > _MAX_TREE_ENTRIES:
                    return total
                if child.is_file():
                    total += child.stat().st_size
                elif child.is_dir():
                    stack.append(child)
        except OSError:
            continue
    return total
