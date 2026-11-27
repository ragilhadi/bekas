"""Conda artifacts plugin for bekas.

Discovers conda package caches (``<root>/pkgs``) and conda environments
from all well-known conda distribution roots plus ``~/.conda/envs``.

Package caches are fully reproducible (conda re-fetches on demand), so
they are SAFE. Environments may hold user-installed packages and
irreplaceable work, so they are REVIEW. The active environment
(``$CONDA_PREFIX``) is never reported and never deleted.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin

CAT_PKGS = "conda.pkgs"
CAT_ENV = "conda.env"

CONDA_ROOT_NAMES = ("miniconda3", "anaconda3", "mambaforge", "micromamba")


class CondaPlugin(Plugin):
    """Finds conda package caches and conda environments.

    Attributes:
        name: Plugin identifier.
        categories: Emitted candidate categories.
        capabilities: Quarantine supported (environments may matter).
    """

    name = "conda.artifacts"
    categories = (CAT_PKGS, CAT_ENV)
    description = "Finds conda package caches and conda environments."
    requires_commands = []
    capabilities = Capabilities(quarantine=True, estimated_runtime="medium")

    def is_available(self, ctx: Context) -> bool:
        """Check if any conda root or user envs directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if a conda distribution root or ``~/.conda/envs`` exists.
        """
        return bool(self._roots()) or self._user_envs_root() is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield conda cache and environment candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects: package caches as SAFE, environments (minus
            the active one) as REVIEW.
        """
        active = self._active_prefix()

        seen: set[Path] = set()
        for root in self._roots():
            pkgs = root / "pkgs"
            if pkgs.is_dir():
                seen.add(pkgs.resolve())
                size = _du(pkgs)
                yield Candidate(
                    id=f"conda.pkgs:{pkgs}",
                    category=CAT_PKGS,
                    size_bytes=size,
                    path_or_handle=str(pkgs),
                    confidence=Confidence.SAFE,
                    reason="Conda package cache (re-downloaded on demand).",
                    metadata={"path": str(pkgs), "root": str(root)},
                )

            envs_dir = root / "envs"
            for env in _child_envs(envs_dir):
                real = _resolved(env)
                if _is_active_env(env, real, active) or real in seen:
                    continue
                seen.add(real)
                yield self._env_candidate(env, active)

        user_envs = self._user_envs_root()
        if user_envs is not None:
            for env in _child_envs(user_envs):
                real = _resolved(env)
                if _is_active_env(env, real, active) or real in seen:
                    continue
                seen.add(real)
                yield self._env_candidate(env, active)

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a conda cache or environment directory.

        Refuses to delete the active environment (``$CONDA_PREFIX``).

        Args:
            candidate: Candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
        active = self._active_prefix()
        if active is not None:
            refusal = "Refusing to delete the active conda environment."
            target = Path(candidate.path_or_handle)
            target_real = _resolved(target)
            active_real = _resolved(active)
            if target == active or target_real == active_real:
                return RemovalResult(success=False, bytes_freed=0, log=refusal)

        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size = _du(path)
            shutil.rmtree(path)
            return RemovalResult(success=True, bytes_freed=size, log="Deleted")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def quarantine(
        self,
        candidate: Candidate,
        ctx: Context,
        quarantine_dir: str,
        run_id: str | None = None,
    ) -> RemovalResult:
        """Quarantine a conda environment instead of deleting it.

        The active environment is refused here as well.

        Args:
            candidate: Candidate to quarantine.
            ctx: Execution context.
            quarantine_dir: Directory to store quarantined items.
            run_id: Optional run identifier for tracking.

        Returns:
            Result of the quarantine attempt.
        """
        from bekas.quarantine import move_to_quarantine

        active = self._active_prefix()
        if active is not None:
            refusal = "Refusing to quarantine the active conda environment."
            target = Path(candidate.path_or_handle)
            target_real = _resolved(target)
            active_real = _resolved(active)
            if target == active or target_real == active_real:
                return RemovalResult(success=False, bytes_freed=0, log=refusal)

        path = Path(candidate.path_or_handle)
        size = _du(path)
        try:
            dest = move_to_quarantine(run_id or "quarantine", path, candidate.category, size, candidate.metadata)
            return RemovalResult(success=True, bytes_freed=size, undo_token=str(dest), log="quarantined")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return True because quarantined environments can be restored.

        Returns:
            True.
        """
        return True

    def _env_candidate(self, env: Path, active: Path | None) -> Candidate:
        """Build a REVIEW candidate for a single environment path.

        Args:
            env: Environment directory.
            active: Active ``$CONDA_PREFIX`` path, if any.

        Returns:
            Candidate for the environment; never called for the active env.
        """
        return Candidate(
            id=f"conda.env:{env}",
            category=CAT_ENV,
            size_bytes=_du(env),
            path_or_handle=str(env),
            confidence=Confidence.REVIEW,
            reason="Conda environment may hold user work.",
            metadata={"path": str(env), "active": str(active) if active is not None else None},
        )

    @staticmethod
    def _roots() -> list[Path]:
        """Return existing conda distribution roots.

        Returns:
            List of existing root Paths (home-relative names).
        """
        home = Path.home()
        return [(home / name) for name in CONDA_ROOT_NAMES if (home / name).is_dir()]

    @staticmethod
    def _user_envs_root() -> Path | None:
        """Return ``~/.conda/envs`` if it exists.

        Returns:
            The path, or None.
        """
        envs = Path.home() / ".conda" / "envs"
        return envs if envs.is_dir() else None

    @staticmethod
    def _active_prefix() -> Path | None:
        """Return the active conda environment path from the environment.

        Returns:
            ``$CONDA_PREFIX`` as a Path, or None when not set.
        """
        prefix = os.environ.get("CONDA_PREFIX")
        return Path(prefix) if prefix else None


def _resolved(path: Path) -> Path:
    """Resolve a path, falling back to the unresolved path on errors.

    Args:
        path: Path to resolve.

    Returns:
        The resolved path, or the original on OSError.
    """
    try:
        return path.resolve()
    except OSError:
        return path


def _is_active_env(env: Path, real: Path, active: Path | None) -> bool:
    """Return True if ``env`` is the active ``$CONDA_PREFIX`` environment.

    Matches raw or realpath equality so symlinked environment layouts work.

    Args:
        env: Candidate environment directory.
        real: ``env`` realpath.
        active: Active prefix or None.

    Returns:
        True when the environment must be skipped in discovery.
    """
    if active is None:
        return False
    return env == active or real == _resolved(active)


def _child_envs(envs_dir: Path) -> Iterator[Path]:
    """Yield environment subdirectories of an envs root.

    Args:
        envs_dir: An ``envs`` directory to enumerate.

    Yields:
        Existing immediate child directories, sorted for determinism.
    """
    try:
        children = sorted(p for p in envs_dir.iterdir() if p.is_dir())
    except OSError:
        return
    yield from children


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
