"""Tests for jupyter_checkpoints plugin."""

from pathlib import Path

from bekas.models import Confidence, Context, RemovalResult
from bekas.plugins.jupyter_checkpoints import CHECKPOINT_DIRNAME, JupyterCheckpointsPlugin, _du, _find_checkpoints


def test_find_checkpoints_finds_dirs(tmp_path: Path) -> None:
    cp = tmp_path / "project" / CHECKPOINT_DIRNAME
    cp.mkdir(parents=True)
    (cp / "notebook-checkpoint.ipynb").write_text("{}")
    found = list(_find_checkpoints(tmp_path))
    assert found == [cp]


def test_find_checkpoints_respects_depth(tmp_path: Path) -> None:
    # Depth 9 from home is beyond the 8-level limit: not reported
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h" / CHECKPOINT_DIRNAME
    deep.mkdir(parents=True)
    found = list(_find_checkpoints(tmp_path))
    assert deep not in found

    # At depth 8 it is within limit if the checkpoint itself is a child:
    cp = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / CHECKPOINT_DIRNAME
    cp.mkdir(parents=True)
    assert cp in list(_find_checkpoints(tmp_path))


def test_find_checkpoints_prunes_node_modules_and_venvs(tmp_path: Path) -> None:
    node = tmp_path / "app" / "node_modules" / "pkg" / CHECKPOINT_DIRNAME
    node.mkdir(parents=True)
    venv = tmp_path / "proj" / ".venv" / CHECKPOINT_DIRNAME
    venv.mkdir(parents=True)
    assert _find_checkpoints(tmp_path) and list(_find_checkpoints(tmp_path)) == []


def test_find_checkpoints_not_recursive_in_checkpoint(tmp_path: Path) -> None:
    cp = tmp_path / "proj" / CHECKPOINT_DIRNAME / CHECKPOINT_DIRNAME
    cp.mkdir(parents=True)
    found = list(_find_checkpoints(tmp_path))
    assert found == [tmp_path / "proj" / CHECKPOINT_DIRNAME]


def test_is_available_false_when_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    p = JupyterCheckpointsPlugin()
    ctx = Context(dry_run=True, config={})
    assert not p.is_available(ctx)


def test_discover_yields_safe_candidates(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cp = home / "notebooks" / CHECKPOINT_DIRNAME
    cp.mkdir(parents=True)
    (cp / "analysis-checkpoint.ipynb").write_text("0123456789")  # 10 bytes
    deep = home / "x" / "y" / "z" / CHECKPOINT_DIRNAME
    deep.mkdir(parents=True)

    p = JupyterCheckpointsPlugin()
    ctx = Context(dry_run=True, config={})
    candidates = list(p.discover(ctx))

    assert len(candidates) == 2
    for c in candidates:
        assert c.category == "jupyter.checkpoints"
        assert c.confidence == Confidence.SAFE
        assert "checkpoint" in c.reason.lower()
    target = next(c for c in candidates if Path(c.path_or_handle) == cp)
    assert target.size_bytes == 10
    assert target.id == f"jupyter.checkpoints:{cp}"


def test_remove_deletes_checkpoint(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    monkeypatch.setattr(Path, "home", lambda: home)
    cp = home / "proj" / CHECKPOINT_DIRNAME
    cp.mkdir(parents=True)
    (cp / "nb-checkpoint.ipynb").write_text("data")
    other = home / "keepme" / "notebook.ipynb"
    other.parent.mkdir(parents=True)
    other.write_text("keep")

    p = JupyterCheckpointsPlugin()
    ctx = Context(dry_run=False, config={})
    candidate = next(iter(p.discover(ctx)))
    result = p.remove(candidate, ctx)

    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 4  # len("data")
    assert not cp.exists()
    assert other.exists()


def test_remove_missing_path_logged(tmp_path: Path) -> None:
    p = JupyterCheckpointsPlugin()
    ctx = Context(dry_run=True, config={})
    candidate = _make_candidate(tmp_path / "gone")
    result = p.remove(candidate, ctx)
    assert result.success is False
    assert result.log == "Path does not exist"


def _make_candidate(path: Path) -> "object":  # type: ignore[no-untyped-def]
    from bekas.models import Candidate

    return Candidate(
        id=f"jupyter.checkpoints:{path}",
        category="jupyter.checkpoints",
        size_bytes=0,
        path_or_handle=str(path),
        confidence=Confidence.SAFE,
        reason="test",
    )


def _boom(*args: object, **kwargs: object) -> None:
    raise OSError("denied")


def test_remove_failure_logged(tmp_path: Path, monkeypatch) -> None:
    cp = tmp_path / "proj" / CHECKPOINT_DIRNAME
    cp.mkdir(parents=True)
    (cp / "f").write_text("x")

    p = JupyterCheckpointsPlugin()
    ctx = Context(dry_run=False, config={})
    candidate = _make_candidate(cp)  # type: ignore[arg-type]
    monkeypatch.setattr("bekas.plugins.jupyter_checkpoints.shutil.rmtree", _boom)
    result = p.remove(candidate, ctx)  # type: ignore[arg-type]
    assert result.success is False
    assert "denied" in result.log
    assert cp.exists()


def test_supports_undo() -> None:
    p = JupyterCheckpointsPlugin()
    assert p.supports_undo() is False


def test_du(tmp_path: Path) -> None:
    d = tmp_path / "a"
    d.mkdir()
    (d / "f").write_text("hello")
    assert _du(d) == 5
