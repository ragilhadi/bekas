"""Python virtual environments plugin for bekas."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class PythonVenvsPlugin(Plugin):
    """Finds orphaned Python virtual environments."""

    name = "python.venvs"
    description = "Finds Python virtual environments in abandoned projects."
    requires_commands = []

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        # Common search roots
        roots = [Path.home() / "code", Path.home() / "projects", Path.home() / "dev", Path.home()]
        roots = [r for r in roots if r.exists()]
        min_idle_days = ctx.config.get("plugin_settings", {}).get("python.venvs", {}).get("min_idle_days", 90)
        cutoff = datetime.now() - timedelta(days=min_idle_days)

        seen: set[Path] = set()
        for root in roots:
            for venv_path in _find_venvs(root, seen):
                project = venv_path.parent
                markers = ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile", "setup.cfg"]
                has_project = any((project / m).exists() for m in markers)
                size = _du(venv_path)

                if not has_project:
                    # SAFE: no project files nearby
                    yield Candidate(
                        id=f"venv:{venv_path}",
                        category="python.venv",
                        size_bytes=size,
                        path_or_handle=str(venv_path),
                        confidence=Confidence.SAFE,
                        reason="Virtual environment in directory with no pyproject.toml / setup.py / requirements.txt.",
                        metadata={"path": str(venv_path), "project": str(project)},
                    )
                else:
                    # Check last access/modification of project marker
                    mtimes = [(project / m).stat().st_mtime for m in markers if (project / m).exists()]
                    if mtimes and datetime.fromtimestamp(max(mtimes)) < cutoff:
                        yield Candidate(
                            id=f"venv:{venv_path}",
                            category="python.venv",
                            size_bytes=size,
                            path_or_handle=str(venv_path),
                            confidence=Confidence.REVIEW,
                            reason=f"Project exists but has not been touched in {min_idle_days}+ days.",
                            metadata={"path": str(venv_path), "project": str(project)},
                        )
                    else:
                        yield Candidate(
                            id=f"venv:{venv_path}",
                            category="python.venv",
                            size_bytes=size,
                            path_or_handle=str(venv_path),
                            confidence=Confidence.MANUAL,
                            reason="Project exists and was recently active.",
                            metadata={"path": str(venv_path), "project": str(project)},
                        )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size = _du(path)
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

    def supports_undo(self) -> bool:
        return True


def _find_venvs(root: Path, seen: set[Path]) -> Iterator[Path]:
    for dirpath, dirnames, _ in os.walk(root):
        dp = Path(dirpath)
        for d in list(dirnames):
            name = d.lower()
            if name in {"venv", ".venv", "env", ".env", "virtualenv"}:
                venv = dp / d
                try:
                    real = venv.resolve()
                except OSError:
                    real = venv
                if real not in seen:
                    seen.add(real)
                    # Validate it looks like a venv
                    if (real / "bin" / "python").exists() or (real / "Scripts" / "python.exe").exists():
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
