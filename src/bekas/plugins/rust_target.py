"""Rust target directories plugin for bekas."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class RustTargetPlugin(Plugin):
    """Finds old Rust target/ directories.

    Scans common project roots for ``target`` directories next to
    ``Cargo.toml`` or containing ``.rustc_info.json``.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required.
    """

    name = "rust.target"
    description = "Finds Rust target/ directories that can be rebuilt."
    requires_commands = []
    capabilities = Capabilities(quarantine=True, estimated_runtime="medium")

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Rust target directory candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing target directories.
        """
        roots = [Path.home() / "code", Path.home() / "projects", Path.home() / "dev", Path.home()]
        roots = [r for r in roots if r.exists()]
        min_idle_days = ctx.config.get("plugin_settings", {}).get("rust.target", {}).get("min_idle_days", 60)
        cutoff = datetime.now() - timedelta(days=min_idle_days)
        seen: set[Path] = set()

        for root in roots:
            for target in _find_targets(root, seen):
                size = _du(target)
                mtime = datetime.fromtimestamp(target.stat().st_mtime)
                if mtime < cutoff:
                    yield Candidate(
                        id=f"rust:{target}",
                        category="rust.target",
                        size_bytes=size,
                        path_or_handle=str(target),
                        confidence=Confidence.SAFE,
                        reason=(
                            f"Rust target/ directory unchanged in {min_idle_days}+ days. "
                            "Safe to delete — `cargo build` rebuilds it."
                        ),
                        metadata={"path": str(target), "mtime": mtime.isoformat()},
                    )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a Rust target directory.

        Args:
            candidate: Target directory candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
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

    def quarantine(
        self,
        candidate: Candidate,
        ctx: Context,
        quarantine_dir: str,
        run_id: str | None = None,
    ) -> RemovalResult:
        """Quarantine a Rust target directory.

        Args:
            candidate: Target directory candidate to quarantine.
            ctx: Execution context.
            quarantine_dir: Directory to store quarantined items.
            run_id: Optional run identifier for tracking.

        Returns:
            Result of the quarantine attempt.
        """
        from bekas.quarantine import move_to_quarantine

        path = Path(candidate.path_or_handle)
        size = _du(path)
        try:
            dest = move_to_quarantine(run_id or "quarantine", path, candidate.category, size, candidate.metadata)
            return RemovalResult(success=True, bytes_freed=size, undo_token=str(dest), log="quarantined")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return True because quarantined targets can be restored.

        Returns:
            Whether undo is supported.
        """
        return True


def _find_targets(root: Path, seen: set[Path]) -> Iterator[Path]:
    """Walk a directory tree and yield Rust target/ directory paths.

    Args:
        root: Directory to walk.
        seen: Set of already-yielded paths to avoid duplicates.

    Yields:
        Resolved paths to target directories.
    """
    for dirpath, dirnames, _ in os.walk(root):
        dp = Path(dirpath)
        for d in list(dirnames):
            if d == "target":
                tp = dp / d
                # Validate it's a Rust target (neighbour has Cargo.toml or target has .rustc_info.json)
                has_cargo = (dp / "Cargo.toml").exists()
                has_rustc_info = (tp / ".rustc_info.json").exists()
                if has_cargo or has_rustc_info:
                    try:
                        real = tp.resolve()
                    except OSError:
                        real = tp
                    if real not in seen:
                        seen.add(real)
                        yield real
                dirnames.remove(d)


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
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total
