"""Git branches plugin for bekas."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class GitBranchesPlugin(Plugin):
    """Finds stale local git branches fully merged into the default branch.

    Scans configured git repositories for branches that are fully merged
    and have not received commits in a configurable number of days.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: List of required external commands.
    """

    name = "git.branches"
    categories = ("git.branch",)
    description = "Finds fully merged local git branches older than a threshold."
    requires_commands = ["git"]
    capabilities = Capabilities(requires_cli=("git",), quarantine=False, estimated_runtime="medium")

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield stale git branch candidates.

        Args:
            ctx: Execution context with git_repos configuration.

        Yields:
            Candidate objects representing stale merged branches.
        """
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
                    sha = _branch_sha(repo, branch)
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
                        metadata={"repo": str(repo), "branch": branch, "default": default, "sha": sha},
                    )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a local git branch, recording a SHA-based undo token.

        Args:
            candidate: Branch candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the branch deletion attempt. On success the
            ``undo_token`` carries ``<repo>|<branch>|<sha>`` when the SHA is
            reachable, so ``undo()`` can recreate the branch.
        """
        repo = Path(candidate.path_or_handle)
        branch = candidate.metadata.get("branch", "")
        sha = candidate.metadata.get("sha")
        try:
            proc = ctx.run_command(["git", "branch", "-d", branch], cwd=repo)
            success = proc.returncode == 0
            token = f"{repo}|{branch}|{sha}" if success and sha else None
            return RemovalResult(
                success=success,
                bytes_freed=0,
                undo_token=token,
                log=proc.stdout + proc.stderr,
            )
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def undo(self, candidate: Candidate, ctx: Context, undo_token: str) -> RemovalResult:
        """Recreate a deleted branch from its recorded SHA.

        Args:
            candidate: Candidate that was removed.
            ctx: Execution context.
            undo_token: ``<repo>|<branch>|<sha>`` token stored at removal time.

        Returns:
            Result of the branch recreation attempt.
        """
        try:
            repo_str, branch, sha = undo_token.split("|", 2)
        except ValueError:
            return RemovalResult(success=False, bytes_freed=0, log=f"Malformed git undo token: {undo_token!r}")
        repo = Path(repo_str)
        if not repo.is_dir():
            return RemovalResult(success=False, bytes_freed=0, log=f"Repository no longer exists: {repo}")
        try:
            proc = ctx.run_command(["git", "branch", branch, sha], cwd=repo)
            success = proc.returncode == 0
            return RemovalResult(success=success, bytes_freed=0, log=proc.stdout + proc.stderr)
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return True: deleted branches can be recreated from the stored SHA.

        Returns:
            Whether undo is supported.
        """
        return True


def _default_branch(repo: Path) -> str | None:
    """Detect the default branch (main or master) of a repository.

    Args:
        repo: Path to the git repository.

    Returns:
        Name of the default branch, or None if neither exists.
    """
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
    """List local branches that are fully merged into the default branch.

    Args:
        repo: Path to the git repository.
        default: Name of the default branch.

    Returns:
        List of merged branch names.
    """
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


def _branch_sha(repo: Path, branch: str) -> str | None:
    """Resolve a branch to its commit SHA, for restorable undo.

    Args:
        repo: Path to the git repository.
        branch: Branch name.

    Returns:
        Full 40-char SHA, or None when it cannot be resolved.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _last_commit_date(repo: Path, branch: str) -> datetime | None:
    """Get the timestamp of the last commit on a branch.

    Args:
        repo: Path to the git repository.
        branch: Branch name.

    Returns:
        Datetime of the last commit, or None if unavailable.
    """
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
