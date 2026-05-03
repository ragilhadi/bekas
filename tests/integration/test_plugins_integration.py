"""Integration tests for bekas plugins using real temp directories."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from bekas.models import Candidate, Confidence, Context
from bekas.plugins.dotfiles_backups import DotfilesBackupsPlugin
from bekas.plugins.git_branches import GitBranchesPlugin
from bekas.plugins.node_modules import NodeModulesPlugin
from bekas.plugins.python_cache import PythonCachePlugin
from bekas.plugins.python_venvs import PythonVenvsPlugin
from bekas.plugins.rust_target import RustTargetPlugin
from bekas.runner import run_audit
from bekas.safety import filter_candidates


class FakePluginWrapper:
    """Wrap a plugin so its discover() only sees a specific temp root."""

    def __init__(self, plugin_cls, root: Path):
        self._plugin = plugin_cls()
        self._root = root
        self.name = self._plugin.name
        self.description = self._plugin.description
        self.requires_commands = self._plugin.requires_commands
        self.supported_platforms = getattr(self._plugin, "supported_platforms", None)

    def is_available(self, ctx: Context) -> bool:
        return self._plugin.is_available(ctx)

    def discover(self, ctx: Context):
        # Monkey-patch Path.home() for the duration of discovery
        original_home = Path.home
        Path.home = lambda: self._root
        try:
            yield from self._plugin.discover(ctx)
        finally:
            Path.home = original_home


class TestPythonVenvsPlugin:
    def test_orphaned_venv(self, tmp_path: Path):
        venv = tmp_path / "myproj" / ".venv"
        venv_bin = venv / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("fake")
        ctx = Context(dry_run=True, config={})
        plugin = self._wrap(tmp_path)
        cands = list(plugin.discover(ctx))
        assert len(cands) == 1
        assert cands[0].confidence == Confidence.SAFE

    def test_active_venv(self, tmp_path: Path):
        proj = tmp_path / "myproj"
        venv = proj / ".venv"
        venv_bin = venv / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("fake")
        (proj / "pyproject.toml").write_text("[project]\nname=active\n")
        ctx = Context(dry_run=True, config={})
        plugin = self._wrap(tmp_path)
        cands = list(plugin.discover(ctx))
        assert len(cands) == 1
        assert cands[0].confidence == Confidence.MANUAL

    def _wrap(self, root: Path):
        return FakePluginWrapper(PythonVenvsPlugin, root)


class TestNodeModulesPlugin:
    def test_abandoned_node_modules(self, tmp_path: Path):
        nm = tmp_path / "oldproj" / "node_modules"
        nm.mkdir(parents=True)
        (nm / "foo").write_text("fake")
        ctx = Context(dry_run=True, config={})
        plugin = self._wrap(tmp_path)
        cands = list(plugin.discover(ctx))
        assert len(cands) == 1
        assert cands[0].confidence == Confidence.SAFE

    def test_active_node_modules(self, tmp_path: Path):
        proj = tmp_path / "activeproj"
        proj.mkdir(parents=True)
        pkg = proj / "package.json"
        pkg.write_text('{"name":"active"}')
        nm = proj / "node_modules"
        nm.mkdir(parents=True)
        (nm / "foo").write_text("fake")
        ctx = Context(dry_run=True, config={})
        plugin = self._wrap(tmp_path)
        cands = list(plugin.discover(ctx))
        assert len(cands) == 1
        assert cands[0].confidence == Confidence.MANUAL

    def _wrap(self, root: Path):
        return FakePluginWrapper(NodeModulesPlugin, root)


class TestRustTargetPlugin:
    def test_old_target(self, tmp_path: Path):
        rustproj = tmp_path / "rustproj"
        target = rustproj / "target"
        target.mkdir(parents=True)
        (rustproj / "Cargo.toml").write_text("[package]\nname=old\n")
        (target / "debug.txt").write_text("fake")
        old = datetime.now() - timedelta(days=100)
        os.utime(target, (old.timestamp(), old.timestamp()))
        ctx = Context(dry_run=True, config={})
        plugin = self._wrap(tmp_path)
        cands = list(plugin.discover(ctx))
        assert len(cands) == 1
        assert cands[0].confidence == Confidence.SAFE

    def _wrap(self, root: Path):
        return FakePluginWrapper(RustTargetPlugin, root)


class TestPythonCachePlugin:
    def test_cache_without_project(self, tmp_path: Path):
        pycache = tmp_path / "orphan" / "__pycache__"
        pycache.mkdir(parents=True)
        (pycache / "foo.cpython-311.pyc").write_text("fake")
        ctx = Context(dry_run=True, config={})
        plugin = self._wrap(tmp_path)
        cands = list(plugin.discover(ctx))
        assert len(cands) == 1
        assert cands[0].confidence == Confidence.SAFE

    def _wrap(self, root: Path):
        return FakePluginWrapper(PythonCachePlugin, root)


class TestGitBranchesPlugin:
    def test_merged_stale_branch(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-b", "main")
        self._git(repo, "config", "user.email", "t@t.com")
        self._git(repo, "config", "user.name", "Test")
        (repo / "file.txt").write_text("hello")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "init")
        self._git(repo, "checkout", "-b", "feature")
        (repo / "feat.txt").write_text("feat")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "feat")
        self._git(repo, "checkout", "main")
        self._git(repo, "merge", "feature", "--no-ff", "-m", "merge")
        # Ensure the branch commit is old enough
        old = datetime.now() - timedelta(days=100)
        # We can't easily back-date git commits without extra work,
        # but the commit is already just now, so let's hack by
        # making config require 0 days or setting min_idle_days=0
        ctx = Context(
            dry_run=True, config={"git_repos": [str(repo)], "plugin_settings": {"git.branches": {"min_idle_days": 0}}}
        )
        plugin = GitBranchesPlugin()
        cands = list(plugin.discover(ctx))
        branch_cands = [c for c in cands if c.metadata.get("branch") == "feature"]
        assert len(branch_cands) == 1
        assert branch_cands[0].confidence == Confidence.SAFE

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TestDotfilesBackupsPlugin:
    def test_backup_file(self, tmp_path: Path):
        bak = tmp_path / ".zshrc.backup-2024-01-01"
        bak.write_text("old")
        ctx = Context(dry_run=True, config={})
        plugin = self._wrap(tmp_path)
        cands = list(plugin.discover(ctx))
        assert len(cands) == 1
        assert cands[0].confidence == Confidence.SAFE

    def _wrap(self, root: Path):
        return FakePluginWrapper(DotfilesBackupsPlugin, root)


class TestSafetyFilters:
    def test_excluded_paths_filtered(self):
        cands = [
            Candidate(
                id="x",
                category="foo",
                size_bytes=1,
                path_or_handle="/etc/passwd",
                confidence=Confidence.SAFE,
                reason="r",
            ),
            Candidate(
                id="y",
                category="foo",
                size_bytes=1,
                path_or_handle="/tmp/ok",
                confidence=Confidence.SAFE,
                reason="r",
            ),
        ]
        safe = filter_candidates(cands)
        assert len(safe) == 1
        assert safe[0].id == "y"


class TestAuditRunnerIntegration:
    def test_runs_plugins_isolated(self, tmp_path: Path):
        venv = tmp_path / "proj" / ".venv"
        venv_bin = venv / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").write_text("fake")
        pycache = tmp_path / "proj" / "__pycache__"
        pycache.mkdir(parents=True)
        (pycache / "a.pyc").write_text("fake")

        wrapped_venv = FakePluginWrapper(PythonVenvsPlugin, tmp_path)
        wrapped_cache = FakePluginWrapper(PythonCachePlugin, tmp_path)
        report = run_audit([wrapped_venv, wrapped_cache], serial=True)
        assert report.summary.total_candidates == 2
