"""Git branches plugin for bekas."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class GitBranchesPlugin(Plugin):
    """Finds stale local git branches fully merged into the default branch."""

    name = "git.branches"
    description = "Finds fully merged local git branches older than a threshold."
    requires_commands = ["git"]

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        repos = ctx.config.get("git_repos", [])
        if not repos:
            return
        min_idle_days = ctx.config.get("plugin_settings", {}).get("git.branches", {}).get("min_idle_days", 90)
        cutoff = datetime.now() - timedelta(days=min_idle_days)

        for raw in repos:
            repo = Path(raw).expanduser().resolve()
            if not (repo / ".git").exists():
                continue
            default = _default_branch(repo)
            if not default:
                continue
            for branch in _merged_branches(repo, default):
                # Skip the default branch itself
                if branch == default:
                    continue
                # Check last commit date on branch
                last_commit = _last_commit_date(repo, branch)
                if last_commit and last_commit < cutoff:
                    yield Candidate(
                        id=f"git:{repo.name}:{branch}",
                        category="git.branch",
                        size_bytes=0,
                        path_or_handle=str(repo),
                        confidence=Confidence.SAFE,
                        reason=(
                            f"Branch '{branch}' is fully merged into '{default}' "
                            f"and has no commits in {min_idle_days}+ days."
                        ),
                        metadata={"repo": str(repo), "branch": branch, "default": default},
                    )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        repo = Path(candidate.path_or_handle)
        branch = candidate.metadata.get("branch", "")
        try:
            proc = ctx.run_command(["git", "branch", "-d", branch], cwd=repo)
            success = proc.returncode == 0
            return RemovalResult(
                success=success,
                bytes_freed=0,
                undo_token=None,
                log=proc.stdout + proc.stderr,
            )
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        return False

    def supports_quarantine(self) -> bool:
        return False


def _default_branch(repo: Path) -> str | None:
    """Detect the default branch (main or master)."""
    for name in ("main", "master"):
        result = shutil.which("git")
        if result:
            proc = subprocess.run(
                ["git", "rev-parse", "--verify", name],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                return name
    return None


def _merged_branches(repo: Path, default: str) -> list[str]:
    proc = subprocess.run(
        ["git", "branch", "--merged", default],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    branches: list[str] = []
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("*"):
            line = line[1:].strip()
        if line:
            branches.append(line)
    return branches


def _last_commit_date(repo: Path, branch: str) -> datetime | None:
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%ct", branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        ts = int(proc.stdout.strip())
        return datetime.fromtimestamp(ts)
    except ValueError:
        return None


import subprocess  # noqa: E402
