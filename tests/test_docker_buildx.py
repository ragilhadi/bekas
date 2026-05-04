"""Tests for docker_buildx plugin."""

import json
from unittest.mock import MagicMock

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.docker_buildx import (
    DockerBuildxPlugin,
    _parse_docker_size,
    _parse_docker_time,
    _parse_human_time,
)


def test_is_available_when_docker_present():
    p = DockerBuildxPlugin()
    # shutil.which in is_available is tested indirectly via monkeypatch in other tests,
    # but here we just assert the method signature works with a context.
    ctx = Context(dry_run=True, config={})
    result = p.is_available(ctx)
    assert isinstance(result, bool)


def test_discover_json_output():
    p = DockerBuildxPlugin()
    ctx = MagicMock(spec=Context)
    ctx.config = {"plugin_settings": {"docker.buildx.cache": {"min_idle_days": 30}}}

    now = "2026-01-01T00:00:00+00:00"
    json_line = json.dumps({
        "ID": "abc123",
        "Size": "5.00GB",
        "LastUsedAt": "2025-01-01T00:00:00+00:00",
        "Mutable": "false",
    })
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json_line
    ctx.run_command = MagicMock(return_value=proc)

    candidates = list(p.discover(ctx))
    assert len(candidates) == 1
    assert candidates[0].category == "docker.buildx.cache"
    assert candidates[0].confidence == Confidence.SAFE
    assert candidates[0].size_bytes == 5 * 1024**3


def test_discovers_no_entries_when_docker_fails():
    p = DockerBuildxPlugin()
    ctx = MagicMock(spec=Context)
    ctx.config = {}
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    ctx.run_command = MagicMock(return_value=proc)

    candidates = list(p.discover(ctx))
    assert candidates == []


def test_remove_calls_prune():
    p = DockerBuildxPlugin()
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "Total: 5GB"
    proc.stderr = ""
    ctx.run_command = MagicMock(return_value=proc)

    c = Candidate(
        id="buildx:abc",
        category="docker.buildx.cache",
        size_bytes=1024,
        path_or_handle="buildx://abc",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"min_idle_days": 30},
    )
    result = p.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    ctx.run_command.assert_called_once_with(
        ["docker", "buildx", "prune", "--filter", "until=30d", "--force"],
        timeout=60,
    )


def test_parse_docker_size_various():
    assert _parse_docker_size("1KB") == 1024
    assert _parse_docker_size("1.5MB") == int(1.5 * 1024**2)
    assert _parse_docker_size("2GB") == 2 * 1024**3
    assert _parse_docker_size("0B") == 0
    assert _parse_docker_size("100") == 100
    assert _parse_docker_size("bogus") == 0


def test_parse_human_time():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    t = _parse_human_time("2 days ago")
    assert t is not None
    assert (now - t).days >= 1  # approximate
    assert _parse_human_time("never") is None
    assert _parse_human_time("") is None
    assert _parse_human_time("1 week ago") is not None


def test_parse_docker_time_iso():
    assert _parse_docker_time("2025-06-15T12:00:00Z") is not None
    assert _parse_docker_time("") is None
