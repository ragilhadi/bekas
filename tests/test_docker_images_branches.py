"""Branch coverage tests for docker_images.py — helper functions, exceptions, size parsing."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from bekas.models import Candidate, Confidence, Context
from bekas.plugins.docker_images import (
    DockerImagesPlugin,
    _container_names_for_image,
    _dangling_images,
    _images_in_use_by_containers,
    _list_images,
    _parse_docker_size,
    _resolve_image_id,
)


class FakeProc:
    """Fake subprocess result for ctx.run_command mocking."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_list_images_json_lines():
    """Parse multiple JSON lines from docker images."""
    ctx = Context(dry_run=True, config={})
    img1 = {"ID": "abc", "Repository": "repo1", "Tag": "v1", "Size": "1MB"}
    img2 = {"ID": "def", "Repository": "repo2", "Tag": "v2", "Size": "2GB"}
    stdout = json.dumps(img1) + "\n" + json.dumps(img2) + "\n"
    with patch.object(ctx, "run_command", return_value=FakeProc(stdout=stdout)):
        images = _list_images(ctx)
        assert len(images) == 2
        assert images[0]["ID"] == "abc"


def test_list_images_bad_json_skipped():
    """Bad JSON lines are skipped gracefully."""
    ctx = Context(dry_run=True, config={})
    stdout = json.dumps({"ID": "ok"}) + "\nnot json\n"
    with patch.object(ctx, "run_command", return_value=FakeProc(stdout=stdout)):
        images = _list_images(ctx)
        assert len(images) == 1


def test_list_images_command_fails():
    """Return empty list when docker images fails."""
    ctx = Context(dry_run=True, config={})
    with patch.object(ctx, "run_command", return_value=FakeProc(returncode=1)):
        images = _list_images(ctx)
        assert images == []


def test_dangling_images_command_fails():
    """Return empty set when docker images --filter dangling fails."""
    ctx = Context(dry_run=True, config={})
    with patch.object(ctx, "run_command", return_value=FakeProc(returncode=1)):
        ids = _dangling_images(ctx)
        assert ids == set()


def test_images_in_use_bad_container_json():
    """Bad container JSON is skipped, not crashed."""
    ctx = Context(dry_run=True, config={})
    stdout = "not json\n"
    with patch.object(ctx, "run_command", return_value=FakeProc(stdout=stdout)):
        ids = _images_in_use_by_containers(ctx)
        assert ids == set()


def test_images_in_use_resolve_failure():
    """When _resolve_image_id fails, the image ID string itself is used."""
    ctx = Context(dry_run=True, config={})
    container = {"Image": "myimage:latest"}
    stdout = json.dumps(container) + "\n"
    with (
        patch.object(ctx, "run_command", return_value=FakeProc(stdout=stdout)),
        patch("bekas.plugins.docker_images._resolve_image_id", return_value=""),
    ):
        ids = _images_in_use_by_containers(ctx)
        # myimage:latest is not a valid ID, so it won't be added
        assert ids == set()


def test_resolve_image_id_success():
    """Resolve returns the inspect output."""
    ctx = Context(dry_run=True, config={})
    with patch.object(ctx, "run_command", return_value=FakeProc(stdout="sha256:abc123\n")):
        result = _resolve_image_id(ctx, "myimage")
        assert result == "sha256:abc123"


def test_resolve_image_id_failure():
    """Resolve returns empty string on failure."""
    ctx = Context(dry_run=True, config={})
    with patch.object(ctx, "run_command", return_value=FakeProc(returncode=1)):
        result = _resolve_image_id(ctx, "myimage")
        assert result == ""


def test_container_names_for_image_no_match():
    """No containers match the image ID."""
    ctx = Context(dry_run=True, config={})
    stdout = json.dumps({"Image": "other", "Names": "foo"}) + "\n"
    with (
        patch.object(ctx, "run_command", return_value=FakeProc(stdout=stdout)),
        patch("bekas.plugins.docker_images._resolve_image_id", return_value="other_id"),
    ):
        names = _container_names_for_image(ctx, "my_id")
        assert names == []


def test_container_names_for_image_bad_json():
    """Bad JSON lines are skipped."""
    ctx = Context(dry_run=True, config={})
    stdout = "not json\n"
    with patch.object(ctx, "run_command", return_value=FakeProc(stdout=stdout)):
        names = _container_names_for_image(ctx, "my_id")
        assert names == []


def test_discover_verbose_exception():
    """When discover crashes and verbose is True, the error is printed."""
    plugin = DockerImagesPlugin()
    ctx = Context(dry_run=True, config={}, verbose=True)
    with (
        patch("bekas.plugins.docker_images._list_images", side_effect=RuntimeError("docker down")),
        patch("builtins.print") as mock_print,
    ):
        cands = list(plugin.discover(ctx))
        assert cands == []
        mock_print.assert_called()


def test_remove_invalid_image_id():
    """remove() rejects invalid image IDs."""
    plugin = DockerImagesPlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="docker:bad id!",
        category="docker.image.dangling",
        size_bytes=1,
        path_or_handle="docker://bad id!",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"image_id": "bad id!"},
    )
    result = plugin.remove(c, ctx)
    assert result.success is False
    assert "Invalid" in result.log


def test_remove_empty_image_id():
    """remove() rejects empty image ID."""
    plugin = DockerImagesPlugin()
    ctx = Context(dry_run=True, config={})
    c = Candidate(
        id="docker:",
        category="docker.image.dangling",
        size_bytes=1,
        path_or_handle="docker://",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"image_id": ""},
    )
    result = plugin.remove(c, ctx)
    assert result.success is False


@pytest.mark.parametrize(
    "size_str,expected",
    [
        ("1B", 1),
        ("1KB", 1024),
        ("1MB", 1024**2),
        ("1GB", 1024**3),
        ("1TB", 1024**4),
        ("1.5GB", int(1.5 * 1024**3)),
        ("2.5MB", int(2.5 * 1024**2)),
        ("  1 GB  ", 1024**3),
        ("123", 123),
        ("0", 0),
        ("invalid", 0),
    ],
)
def test_parse_docker_size(size_str, expected):
    assert _parse_docker_size(size_str) == expected
