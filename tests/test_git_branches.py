"""Tests for git_branches plugin."""

from unittest.mock import MagicMock

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.git_branches import GitBranchesPlugin


def test_discover_with_mock_git():
    p = GitBranchesPlugin()
    ctx = MagicMock(spec=Context)
    ctx.config = {"git_repos": ["/tmp/fake_repo"], "plugin_settings": {"git.branches": {"min_idle_days": 90}}}
    candidates = list(p.discover(ctx))
    assert isinstance(candidates, list)


def test_remove_deletes_branch():
    p = GitBranchesPlugin()
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=0, stdout="", stderr="")
    ctx.run_command = MagicMock(return_value=proc)

    c = Candidate(
        id="git:feature/x",
        category="git.branch",
        size_bytes=0,
        path_or_handle="/tmp/repo",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"repo": "/tmp/repo", "branch": "feature/x"},
    )
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True


def test_supports():
    p = GitBranchesPlugin()
    assert p.supports_quarantine() is False
    # Branch deletion is undoable via the SHA recorded at discovery.
    assert p.supports_undo() is True


def test_branch_sha_resolves_real_repo(tmp_path):
    """_branch_sha returns the full SHA of an existing branch."""
    import subprocess as sp

    from bekas.plugins.git_branches import _branch_sha

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "c1"],
        cwd=repo,
        check=True,
    )
    sha = sp.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    sp.run(["git", "branch", "feature/x"], cwd=repo, check=True)
    assert _branch_sha(repo, "feature/x") == sha
    assert _branch_sha(repo, "no/such") is None


def test_delete_then_undo_recreates_branch(tmp_path):
    """Full cycle: branch with SHA -> delete via remove() -> undo() recreates it."""
    import subprocess as sp

    p = GitBranchesPlugin()
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "c1"],
        cwd=repo,
        check=True,
    )
    sp.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "c2"],
        cwd=repo,
        check=True,
    )
    old_sha = sp.run(
        ["git", "rev-parse", "HEAD~1"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    sp.run(["git", "branch", "stalefeat"], cwd=repo, check=True)

    class _Ctx:
        dry_run = False
        config: dict = {}

        def run_command(self, cmd, cwd=None):
            return sp.run(cmd, cwd=cwd, capture_output=True, text=True)

        def which(self, name):
            import shutil

            return shutil.which(name)

    ctx = _Ctx()
    c = Candidate(
        id="git:repo:stalefeat",
        category="git.branch",
        size_bytes=0,
        path_or_handle=str(repo),
        confidence=Confidence.SAFE,
        reason="r",
        metadata={"repo": str(repo), "branch": "stalefeat", "sha": old_sha},
    )
    result = p.remove(c, ctx)
    assert result.success is True, result.log
    branches = sp.run(
        ["git", "branch", "--list", "stalefeat"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "stalefeat" not in branches

    undo_result = p.undo(c, ctx, result.undo_token)
    assert undo_result.success is True, undo_result.log
    assert (
        sp.run(["git", "rev-parse", "stalefeat"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
        == old_sha
    )


def test_undo_malformed_token_fails_cleanly():
    p = GitBranchesPlugin()

    class _Ctx:
        dry_run = False
        config: dict = {}

        def run_command(self, cmd, cwd=None):
            raise AssertionError("should not run git")

        def which(self, name):
            return None

    result = p.undo(
        Candidate(
            id="x", category="git.branch", size_bytes=0, path_or_handle="/r", confidence=Confidence.SAFE, reason=""
        ),
        _Ctx(),
        "garbage",
    )
    assert result.success is False
    assert "Malformed" in result.log
