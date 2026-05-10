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
    assert p.supports_undo() is False
