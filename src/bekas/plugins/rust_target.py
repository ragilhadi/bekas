"""Rust target directories plugin for bekas."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class RustTargetPlugin(Plugin):
    """Finds old Rust target/ directories."""

    name = "rust.target"
    description = "Finds Rust target/ directories that can be rebuilt."
    requires_commands = []

    def discover(self, ctx: Context) -> Iterator[Candidate]:
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

    def supports_quarantine(self) -> bool:
        return True

    def quarantine(self, candidate: Candidate, ctx: Context, quarantine_dir: str) -> RemovalResult:
        from bekas.quarantine import move_to_quarantine

        path = Path(candidate.path_or_handle)
        size = _du(path)
        try:
            dest = move_to_quarantine("quarantine", path, candidate.category, size, candidate.metadata)
            return RemovalResult(success=True, bytes_freed=size, undo_token=str(dest), log="quarantined")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))


def _find_targets(root: Path, seen: set[Path]) -> Iterator[Path]:
    for dirpath, dirnames, _ in os.walk(root):
        dp = Path(dirpath)
        for d in list(dirnames):
            if d == "target":
                target = dp / d
                # Validate it looks like a Rust target (contains .rustc_info.json or is next to Cargo.toml)
                cargo_toml = dp / "Cargo.toml"
                rustc_info = target / ".rustc_info.json"
                if cargo_toml.exists() or rustc_info.exists():
                    try:
                        real = target.resolve()
                    except OSError:
                        real = target
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
