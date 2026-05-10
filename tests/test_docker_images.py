"""Tests for docker_images plugin."""

from unittest.mock import MagicMock, patch

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugins.docker_images import (
    DockerImagesPlugin,
    _container_names_for_image,
    _dangling_images,
    _images_in_use_by_containers,
    _list_images,
    _parse_docker_size,
    _resolve_image_id,
)


def test_parse_docker_size():
    assert _parse_docker_size("1.5GB") == int(1.5 * 1024**3)
    assert _parse_docker_size("500MB") == 500 * 1024**2
    assert _parse_docker_size("100KB") == 100 * 1024
    assert _parse_docker_size("10B") == 10
    assert _parse_docker_size("") == 0
    assert _parse_docker_size("bogus") == 0


def test_list_images_empty_on_failure():
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    ctx.run_command = MagicMock(return_value=proc)
    assert _list_images(ctx) == []


def test_list_images_parses_json():
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = '{"ID":"abc123","Repository":"foo","Tag":"latest","Size":"100MB"}\n'
    ctx.run_command = MagicMock(return_value=proc)
    imgs = _list_images(ctx)
    assert len(imgs) == 1
    assert imgs[0]["ID"] == "abc123"


def test_dangling_images_empty_on_failure():
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    ctx.run_command = MagicMock(return_value=proc)
    assert _dangling_images(ctx) == set()


def test_images_in_use_empty_on_failure():
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    ctx.run_command = MagicMock(return_value=proc)
    assert _images_in_use_by_containers(ctx) == set()


def test_resolve_image_id():
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "sha256:abc123\n"
    ctx.run_command = MagicMock(return_value=proc)
    assert _resolve_image_id(ctx, "foo:latest") == "sha256:abc123"


def test_container_names_empty_on_failure():
    ctx = MagicMock(spec=Context)
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    ctx.run_command = MagicMock(return_value=proc)
    assert _container_names_for_image(ctx, "abc") == []


def test_remove_invalid_id():
    p = DockerImagesPlugin()
    ctx = MagicMock(spec=Context)
    c = Candidate(
        id="docker:bad",
        category="docker.image.dangling",
        size_bytes=0,
        path_or_handle="docker://bad",
        confidence=Confidence.SAFE,
        reason="test",
        metadata={"image_id": "../../etc/passwd"},
    )
    result = p.remove(c, ctx)
    assert result.success is False


def test_discover_yields_tagged_image():
    """Discover should yield a non-dangling, unused image as REVIEW."""
    p = DockerImagesPlugin()

    list_proc = MagicMock(
        returncode=0,
        stdout='{"ID":"abc","Repository":"foo","Tag":"latest","Size":"100MB","CreatedAt":"2024-01-01 00:00:00"}\n',
    )
    dangling_proc = MagicMock(returncode=0, stdout="")
    ps_proc = MagicMock(returncode=1, stdout="")

    def side_effect(cmd, **kwargs):
        base = cmd[1] if len(cmd) > 1 else ""
        if base == "images":
            if any("dangling=true" in str(a) for a in cmd):
                return dangling_proc
            return list_proc
        if base == "ps":
            return ps_proc
        return MagicMock(returncode=1, stdout="", stderr="")

    with patch("bekas.plugins.docker_images._resolve_image_id", return_value=""):
        ctx = MagicMock(spec=Context)
        ctx.run_command = MagicMock(side_effect=side_effect)
        candidates = list(p.discover(ctx))
        assert len(candidates) >= 1
        assert candidates[0].category == "docker.image.tagged"
        assert candidates[0].confidence == Confidence.REVIEW

        result = p.remove(candidates[0], ctx)
        assert isinstance(result, RemovalResult)
