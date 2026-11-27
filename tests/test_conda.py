"""Tests for conda plugin."""

from pathlib import Path

import pytest

from bekas.models import Confidence, Context, RemovalResult
from bekas.plugins.conda import CondaPlugin, _du


def test_is_available_false(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)


def test_is_available_true_for_root(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    (home / "miniconda3").mkdir()
    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)


def test_is_available_true_for_user_envs(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    (home / ".conda" / "envs").mkdir(parents=True)
    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    assert p.is_available(ctx)


def test_discover_pkgs_safe_envs_review(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    root = home / "miniconda3"
    pkgs = root / "pkgs"
    pkgs.mkdir(parents=True)
    (pkgs / "nltk-3.8").mkdir()
    (pkgs / "nltk-3.8" / "info").mkdir()
    (pkgs / "nltk-3.8" / "info" / "files").write_text("hello")  # 5 bytes
    envs = root / "envs"
    (envs / "data-science").mkdir(parents=True)
    (envs / "data-science" / "bin").mkdir()
    (envs / "data-science" / "bin" / "python").write_text("#!/bin/python")
    user_envs = home / ".conda" / "envs"
    (user_envs / "scratch").mkdir(parents=True)
    (user_envs / "scratch" / "conda-meta").mkdir()

    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    for c in candidates:
        print("CANDIDATE:", c.id, c.category, c.confidence)

    by_path = {Path(c.path_or_handle): c for c in candidates}
    assert len(candidates) == 3

    pkgs_c = by_path[pkgs]
    assert pkgs_c.category == "conda.pkgs"
    assert pkgs_c.confidence == Confidence.SAFE
    assert pkgs_c.size_bytes == 5

    env_c = by_path[envs / "data-science"]
    assert env_c.category == "conda.env"
    assert env_c.confidence == Confidence.REVIEW
    assert "user work" in env_c.reason
    assert (envs / "data-science") in {(envs / "data-science")}

    user_c = by_path[user_envs / "scratch"]
    assert user_c.category == "conda.env"
    assert user_c.confidence == Confidence.REVIEW


def test_discover_multiple_roots(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    for name in ("miniconda3", "anaconda3", "mambaforge", "micromamba"):
        (home / name / "pkgs").mkdir(parents=True)
    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 4
    assert all(c.category == "conda.pkgs" and c.confidence == Confidence.SAFE for c in candidates)


def test_discover_skips_active_env(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    envs = home / "miniconda3" / "envs"
    (envs / "prod").mkdir(parents=True)
    (envs / "scratch").mkdir(parents=True)
    monkeypatch.setenv("CONDA_PREFIX", str(envs / "prod"))

    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    paths = [c.path_or_handle for c in candidates]
    assert str(envs / "prod") not in paths
    assert str(envs / "scratch") in paths
    assert all(c.confidence == Confidence.REVIEW for c in candidates)


def test_discover_dedupes_roots_and_user_envs(tmp_path: Path, monkeypatch) -> None:
    # A root's envs dir and ~/.conda/envs pointing at the same env via
    # duplicate listing must not produce two candidates.
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    envs = home / "miniconda3" / "envs"
    (envs / "shared").mkdir(parents=True)
    (home / ".conda").mkdir()
    (home / ".conda" / "envs").symlink_to(envs)

    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert [c.path_or_handle for c in candidates] == [str(envs / "shared")]


def test_remove_deletes_env_but_not_active(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    envs = home / "miniconda3" / "envs"
    active = envs / "prod"
    active.mkdir(parents=True)
    (active / "conda-meta").mkdir()
    (active / "conda-meta" / "history").write_text("keep")
    scratch = envs / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "marker").write_text("bye")
    monkeypatch.setenv("CONDA_PREFIX", str(active))

    p = CondaPlugin()
    ctx = Context(dry_run=False, config={})

    # Active env: discovery skipped it, so build a candidate manually and
    # confirm remove refuses.
    from bekas.models import Candidate

    active_cand = Candidate(
        id=f"conda.env:{active}",
        category="conda.env",
        size_bytes=0,
        path_or_handle=str(active),
        confidence=Confidence.REVIEW,
        reason="test",
    )
    result = p.remove(active_cand, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is False
    assert "active" in result.log.lower()
    assert active.exists()

    # Non-active env is deleted.
    result2 = p.remove(
        Candidate(
            id=f"conda.env:{scratch}",
            category="conda.env",
            size_bytes=_du(scratch),
            path_or_handle=str(scratch),
            confidence=Confidence.REVIEW,
            reason="test",
        ),
        ctx,
    )
    assert result2.success is True
    assert result2.log == "Deleted"
    assert not scratch.exists()
    assert active.exists()


def test_remove_pkgs(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    pkgs = home / "micromamba" / "pkgs"
    pkgs.mkdir(parents=True)
    (pkgs / "numpy").write_text("12345")

    p = CondaPlugin()
    ctx = Context(dry_run=False, config={})
    candidate = next(iter(p.discover(ctx)))
    assert candidate.category == "conda.pkgs"
    result = p.remove(candidate, ctx)
    assert result.success is True
    assert result.bytes_freed == 5
    assert not pkgs.exists()


def test_categories_and_name() -> None:
    p = CondaPlugin()
    assert p.name == "conda.artifacts"
    assert p.categories == ("conda.pkgs", "conda.env")


def test_remove_failure_logged(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "f").write_text("x")

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("denied")

    p = CondaPlugin()
    ctx = Context(dry_run=False, config={})
    monkeypatch.setattr("bekas.plugins.conda.shutil.rmtree", _boom)
    from bekas.models import Candidate

    result = p.remove(
        Candidate(
            id=f"conda.env:{env}",
            category="conda.env",
            size_bytes=1,
            path_or_handle=str(env),
            confidence=Confidence.REVIEW,
            reason="test",
        ),
        ctx,
    )
    assert result.success is False
    assert "denied" in result.log
    assert env.exists()


@pytest.mark.parametrize("name", ["miniconda3", "anaconda3", "mambaforge", "micromamba"])
def test_each_root_discovered(tmp_path: Path, monkeypatch, name: str) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    root = home / name
    pkgs = root / "pkgs"
    pkgs.mkdir(parents=True)
    p = CondaPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    assert candidates[0].path_or_handle == str(pkgs)
    assert candidates[0].confidence == Confidence.SAFE


def test_du(tmp_path: Path) -> None:
    d = tmp_path / "a"
    d.mkdir()
    (d / "f").write_text("hello")
    assert _du(d) == 5
