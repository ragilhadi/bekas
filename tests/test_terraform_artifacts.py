"""Tests for the terraform_artifacts plugin."""

from __future__ import annotations

from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.terraform_artifacts import TerraformArtifactsPlugin, _du, _is_terraform_project


def _ctx(dry_run: bool = False) -> Context:
    return Context(dry_run=dry_run, config={})


def _make_project(tmp_path: Path, name: str = "proj") -> tuple[Path, Path]:
    """Create a Terraform project with a .terraform working dir."""
    project = tmp_path / name
    (project / ".terraform" / "providers" / "registry.terraform.io" / "hashicorp" / "aws").mkdir(parents=True)
    (project / ".terraform" / "modules" / "vpc").mkdir(parents=True)
    (project / ".terraform" / "providers" / "terraform-provider-aws.zip").write_text("provider binary" * 100)
    (project / ".terraform" / "modules" / "modules.json").write_text("{}")
    (project / "main.tf").write_text('provider "aws" {}')
    (project / "terraform.lock.hcl").write_text("# lockfile")
    return project, project / ".terraform"


def test_not_available_when_no_terraform_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plugin = TerraformArtifactsPlugin()
    assert plugin.is_available(_ctx()) is False
    assert list(plugin.discover(_ctx())) == []


def test_discover_finds_project_artifacts(tmp_path, monkeypatch):
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    (home / "code").mkdir()
    project, tf_dir = _make_project(home / "code")
    plugin = TerraformArtifactsPlugin()
    candidates = list(plugin.discover(_ctx()))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.path_or_handle == str(tf_dir)
    assert c.confidence == Confidence.REVIEW
    assert c.category == "terraform.artifacts"
    assert c.size_bytes > 0
    assert c.metadata["lockfile"] == str(project / "terraform.lock.hcl")


def test_discover_ignores_terraform_dir_without_project(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stray = tmp_path / "stray" / ".terraform"
    stray.mkdir(parents=True)
    (stray / "providers").mkdir()
    plugin = TerraformArtifactsPlugin()
    assert list(plugin.discover(_ctx())) == []
    assert plugin.is_available(_ctx()) is False


def test_lockfile_alone_is_a_project(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "terraform.lock.hcl").write_text("# lockfile")
    (project / ".terraform").mkdir()
    assert _is_terraform_project(project) is True
    assert _is_terraform_project(tmp_path) is False


def test_walk_skips_node_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    buried = tmp_path / "code" / "app" / "node_modules" / "pkg" / "tfproj"
    (buried / ".terraform").mkdir(parents=True)
    (buried / "main.tf").write_text("x")
    plugin = TerraformArtifactsPlugin()
    assert list(plugin.discover(_ctx())) == []


def test_remove_deletes_providers_and_modules_only(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project, tf_dir = _make_project(tmp_path)
    plugin = TerraformArtifactsPlugin()
    c = Candidate(
        id=f"terraform:{tf_dir}",
        category="terraform.artifacts",
        size_bytes=0,
        path_or_handle=str(tf_dir),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"path": str(tf_dir)},
    )
    result = plugin.remove(c, _ctx())
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed > 0
    assert not (tf_dir / "providers").exists()
    assert not (tf_dir / "modules").exists()
    # Anything outside the allowlist survives: the dir itself, the lockfile, and state files.
    assert tf_dir.is_dir()
    assert (project / "terraform.lock.hcl").exists()
    # A terraform.tf inside .terraform is not part of the allowlist.
    (tf_dir / "terraform.tf").write_text("state")
    (tf_dir / "providers").mkdir()
    plugin.remove(c, _ctx())
    assert (project / "terraform.lock.hcl").exists()
    assert (tf_dir / "terraform.tf").exists()


def test_remove_dry_run_leaves_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _, tf_dir = _make_project(tmp_path)
    plugin = TerraformArtifactsPlugin()
    c = Candidate(
        id=f"terraform:{tf_dir}",
        category="terraform.artifacts",
        size_bytes=0,
        path_or_handle=str(tf_dir),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"path": str(tf_dir)},
    )
    result = plugin.remove(c, _ctx(dry_run=True))
    assert result.success is True
    assert result.log == "dry-run"
    assert result.bytes_freed == 0
    assert (tf_dir / "providers").exists()
    assert (tf_dir / "modules").exists()


def test_remove_rejects_non_terraform_dir(tmp_path):
    plugin = TerraformArtifactsPlugin()
    c = Candidate(
        id="terraform:bad",
        category="terraform.artifacts",
        size_bytes=0,
        path_or_handle=str(tmp_path / "not-terraform"),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={},
    )
    result = plugin.remove(c, _ctx())
    assert result.success is False
    assert "Not a .terraform directory" in result.log


def test_remove_empty_dir_reports_nothing_deleted(tmp_path):
    tf_dir = tmp_path / ".terraform"
    tf_dir.mkdir()
    plugin = TerraformArtifactsPlugin()
    c = Candidate(
        id=f"terraform:{tf_dir}",
        category="terraform.artifacts",
        size_bytes=0,
        path_or_handle=str(tf_dir),
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={},
    )
    result = plugin.remove(c, _ctx())
    assert result.success is False
    assert result.bytes_freed == 0


def test_supports_undo_and_quarantine():
    plugin = TerraformArtifactsPlugin()
    assert plugin.supports_undo() is False
    assert plugin.supports_quarantine() is False


def test_du_measures_files(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    (d / "f1").write_text("hello")
    (d / "f2").write_text("world!")
    assert _du(d) >= 10  # exact byte count varies across filesystems
    assert _du(tmp_path / "missing") == 0
