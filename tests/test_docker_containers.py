"""Tests for docker_containers plugin."""

from unittest.mock import MagicMock

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.docker_containers import DockerContainersPlugin


def test_is_available():
    p = DockerContainersPlugin()
    ctx = Context(dry_run=True, config={})
    assert isinstance(p.is_available(ctx), bool)


def test_discover_parses_json():
    p = DockerContainersPlugin()
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = '{"ID":"abc123","Names":"my_container","Image":"foo:latest","CreatedAt":"2024-01-01"}\n'
    ctx.run_command = MagicMock(return_value=proc)

    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    assert candidates[0].category == "docker.container.stopped"
    assert candidates[0].confidence == Confidence.SAFE


def test_discover_empty_on_failure():
    p = DockerContainersPlugin()
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    ctx.run_command = MagicMock(return_value=proc)

    candidates = list(p.discover(ctx))
    assert candidates == []


def test_remove():
    p = DockerContainersPlugin()
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = ""
    proc.stderr = ""
    ctx.run_command = MagicMock(return_value=proc)

    c = Candidate(
        id="docker-container:abc",
        category="docker.container.stopped",
        size_bytes=0,
        path_or_handle="docker-container://abc",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"container_id": "abc"},
    )
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True


def test_supports():
    p = DockerContainersPlugin()
    assert p.supports_quarantine() is False
    assert p.supports_undo() is False
