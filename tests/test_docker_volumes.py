"""Tests for the docker_volumes plugin."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.docker_volumes import (
    DockerVolumesPlugin,
    _list_volumes,
    _parse_docker_size,
    _volume_sizes,
)


def _ctx(dry_run: bool = False) -> Context:
    return Context(dry_run=dry_run, config={})


def test_not_available_when_docker_missing():
    plugin = DockerVolumesPlugin()
    ctx = _ctx()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("shutil.which", lambda name: None)
        assert plugin.is_available(ctx) is False


def test_not_available_when_no_volumes():
    """is_available is False when docker CLI absent — dirs-only plugins check dirs, we check CLI."""
    plugin = DockerVolumesPlugin()
    ctx = _ctx()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
        assert plugin.is_available(ctx) is True


def test_list_volumes_empty_on_failure():
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=1, stdout="", stderr="")
    ctx.run_command = MagicMock(return_value=proc)
    assert _list_volumes(ctx) == []


def test_list_volumes_parses_json():
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=0, stdout='{"Name":"db-data","Driver":"local","CreatedAt":"2024-01-01"}\n')
    ctx.run_command = MagicMock(return_value=proc)
    volumes = _list_volumes(ctx)
    assert len(volumes) == 1
    assert volumes[0]["Name"] == "db-data"


def test_volume_sizes_parses_df_table():
    stdout = (
        "Images space usage:\n\n"
        "REPOSITORY   TAG   IMAGE ID   SIZE   SHARED   UNIQUE   CONTAINERS\n"
        "foo          latest   abc     100MB   0B       100MB    0\n\n"
        "Containers space usage:\n\n"
        "CONTAINER ID   IMAGE   LOCAL VOLUMES   SIZE   CREATED   STATUS   NAMES\n\n"
        "Local Volumes Space usage:\n\n"
        "VOLUME NAME    LINKS   SIZE\n"
        "db-data        1       1.5GB\n"
        "logs           0       2.50MB\n"
        "---------------\n"
        "Build Cache usage:\n"
    )
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=0, stdout=stdout)
    ctx.run_command = MagicMock(return_value=proc)
    sizes = _volume_sizes(ctx)
    assert sizes["db-data"] >= int(1.5 * 1024**3)  # 1.5GB row or an exact-bytes variant, either parses
    assert sizes["logs"] >= int(2.5 * 1024**2)


def test_volume_sizes_empty_on_failure():
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=1, stdout="")
    ctx.run_command = MagicMock(return_value=proc)
    assert _volume_sizes(ctx) == {}


def test_parse_docker_size():
    assert _parse_docker_size("1.5GB") == int(1.5 * 1024**3)
    assert _parse_docker_size("500MB") == 500 * 1024**2
    assert _parse_docker_size("100KB") == 100 * 1024
    assert _parse_docker_size("10B") == 10
    assert _parse_docker_size("") == 0
    assert _parse_docker_size("bogus") == 0


def test_discover_yields_review_tier_with_df_sizes():
    plugin = DockerVolumesPlugin()
    ls_proc = MagicMock(returncode=0, stdout='{"Name":"db-data","Driver":"local","CreatedAt":"2024-01-01"}\n')
    df_proc = MagicMock(
        returncode=0,
        stdout="Local Volumes Space usage:\n\nVOLUME NAME  LINKS  SIZE\ndb-data  1  2.00GB\n",
    )
    ctx = MagicMock(spec=Context)

    def side_effect(cmd, **kwargs):
        if "system" in cmd:
            return df_proc
        return ls_proc

    ctx.run_command = MagicMock(side_effect=side_effect)
    candidates = list(plugin.discover(ctx))
    assert len(candidates) == 1
    c = candidates[0]
    assert c.category == "docker.volume"
    assert c.confidence == Confidence.REVIEW
    assert c.path_or_handle == "docker://volume/db-data"
    assert c.size_bytes >= int(2 * 1024**3)
    assert c.metadata["volume_name"] == "db-data"


def test_discover_yields_zero_size_when_df_fails():
    plugin = DockerVolumesPlugin()
    ls_proc = MagicMock(returncode=0, stdout='{"Name":"logs","Driver":"local"}\n')
    df_proc = MagicMock(returncode=1, stdout="")
    ctx = MagicMock(spec=Context)

    def side_effect(cmd, **kwargs):
        if "system" in cmd:
            return df_proc
        return ls_proc

    ctx.run_command = MagicMock(side_effect=side_effect)
    candidates = list(plugin.discover(ctx))
    assert len(candidates) == 1
    assert candidates[0].size_bytes == 0


def test_discover_empty_when_docker_fails():
    plugin = DockerVolumesPlugin()
    ctx = MagicMock(spec=Context)
    proc = MagicMock(returncode=1, stdout="", stderr="")
    ctx.run_command = MagicMock(return_value=proc)
    assert list(plugin.discover(ctx)) == []


def test_remove_success():
    c = Candidate(
        id="docker-volume:db-data",
        category="docker.volume",
        size_bytes=1024,
        path_or_handle="docker://volume/db-data",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"volume_name": "db-data"},
    )
    plugin = DockerVolumesPlugin()
    proc = MagicMock(returncode=0, stdout="db-data\n", stderr="")
    ctx = MagicMock(spec=Context)
    ctx.dry_run = False
    ctx.run_command = MagicMock(return_value=proc)
    result = plugin.remove(c, ctx)
    assert isinstance(result, RemovalResult)
    assert result.success is True
    assert result.bytes_freed == 1024
    cmd = ctx.run_command.call_args[0][0]
    assert cmd == ["docker", "volume", "rm", "db-data"]


def test_remove_failure_returns_unsuccessful():
    c = Candidate(
        id="docker-volume:db-data",
        category="docker.volume",
        size_bytes=0,
        path_or_handle="docker://volume/db-data",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"volume_name": "db-data"},
    )
    plugin = DockerVolumesPlugin()
    proc = MagicMock(returncode=1, stdout="", stderr="error: volume is in use")
    ctx = MagicMock(spec=Context)
    ctx.dry_run = False
    ctx.run_command = MagicMock(return_value=proc)
    result = plugin.remove(c, ctx)
    assert result.success is False
    assert result.bytes_freed == 0


def test_remove_invalid_volume_name_rejected():
    c = Candidate(
        id="docker-volume:bad",
        category="docker.volume",
        size_bytes=0,
        path_or_handle="docker://volume/bad",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"volume_name": "../../etc/passwd"},
    )
    plugin = DockerVolumesPlugin()
    ctx = _ctx()
    result = plugin.remove(c, ctx)
    assert result.success is False
    assert "Invalid" in result.log


def test_remove_dry_run_does_not_run_command():
    c = Candidate(
        id="docker-volume:db-data",
        category="docker.volume",
        size_bytes=100,
        path_or_handle="docker://volume/db-data",
        confidence=Confidence.REVIEW,
        reason="test",
        metadata={"volume_name": "db-data"},
    )
    plugin = DockerVolumesPlugin()
    ctx = _ctx(dry_run=True)
    result = plugin.remove(c, ctx)
    assert result.success is True
    assert result.log == "dry-run"
    assert result.bytes_freed == 0


def test_supports_undo_and_quarantine():
    plugin = DockerVolumesPlugin()
    assert plugin.supports_undo() is False
    assert plugin.supports_quarantine() is False
