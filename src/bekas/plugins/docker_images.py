"""Docker images plugin for bekas."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from typing import Any

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class DockerImagesPlugin(Plugin):
    """Finds dangling and unused Docker images.

    Discovers Docker images that are dangling (no tags, no children) or
    unused by any container, and classifies them by safety tier.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: List of required external commands.
    """

    name = "docker.images"
    description = "Finds dangling and unused Docker images."
    requires_commands = ["docker"]
    capabilities = Capabilities(requires_cli=("docker",), quarantine=False, estimated_runtime="medium")

    def is_available(self, ctx: Context) -> bool:
        """Check if the ``docker`` command is present on PATH.

        Args:
            ctx: Execution context.

        Returns:
            True if Docker is available.
        """
        return shutil.which("docker") is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield Docker image candidates.

        Args:
            ctx: Execution context.

        Yields:
            Candidate objects representing Docker images.
        """
        try:
            in_use_ids = _images_in_use_by_containers(ctx)
            dangling_ids = _dangling_images(ctx)
            for img in _list_images(ctx):
                img_id = img.get("ID", "")
                repo = img.get("Repository", "<none>")
                tag = img.get("Tag", "<none>")
                size_str = img.get("Size", "0B")
                created = img.get("CreatedAt", "")
                size_bytes = _parse_docker_size(size_str)
                is_dangling = img_id in dangling_ids
                used_by_container = img_id in in_use_ids

                if is_dangling and not used_by_container:
                    yield Candidate(
                        id=f"docker:{img_id}",
                        category="docker.image.dangling",
                        size_bytes=size_bytes,
                        path_or_handle=f"docker://{img_id}",
                        confidence=Confidence.SAFE,
                        reason="Dangling image with no tags and no descendant images.",
                        metadata={"image_id": img_id, "created": created, "tags": []},
                    )
                else:
                    reason_parts = [f"Tagged image {repo}:{tag}."]
                    if used_by_container:
                        cnames = _container_names_for_image(ctx, img_id)
                        reason_parts.append(
                            f"Currently used by container(s): {', '.join(cnames)}. " "Remove the container first."
                        )
                        conf = Confidence.MANUAL
                    else:
                        reason_parts.append("Not currently used by any container.")
                        conf = Confidence.REVIEW
                    yield Candidate(
                        id=f"docker:{img_id}",
                        category="docker.image.tagged",
                        size_bytes=size_bytes,
                        path_or_handle=f"docker://{img_id}",
                        confidence=conf,
                        reason=" ".join(reason_parts),
                        metadata={"image_id": img_id, "repo": repo, "tag": tag, "created": created},
                    )
        except Exception as exc:
            if ctx.verbose:
                import traceback

                print(f"docker.images discover error: {exc}")
                traceback.print_exc()

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Remove a Docker image.

        Args:
            candidate: Docker image candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the removal attempt.
        """
        img_id = candidate.metadata.get("image_id", candidate.id.replace("docker:", ""))
        # Validate image ID: must be alphanumeric with colon/hyphen/underscore, no spaces or shell metacharacters
        if not img_id or not re.fullmatch(r"[A-Za-z0-9._:\-]+", img_id):
            return RemovalResult(success=False, bytes_freed=0, log=f"Invalid or unsafe image ID: {img_id!r}")
        proc = ctx.run_command(["docker", "rmi", img_id])
        success = proc.returncode == 0
        return RemovalResult(
            success=success,
            bytes_freed=candidate.size_bytes if success else 0,
            undo_token=None,
            log=proc.stdout + proc.stderr,
        )

    def supports_undo(self) -> bool:
        """Return False because Docker image removal cannot be undone.

        Returns:
            Whether undo is supported.
        """
        return False


def _list_images(ctx: Context) -> list[dict[str, Any]]:
    """List all Docker images as dictionaries.

    Args:
        ctx: Execution context.

    Returns:
        List of image metadata dictionaries.
    """
    proc = ctx.run_command(["docker", "images", "--format", "json"])
    if proc.returncode != 0:
        return []
    images: list[dict[str, Any]] = []
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            images.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return images


def _dangling_images(ctx: Context) -> set[str]:
    """Return IDs of images that are dangling (no tags, no children).

    Args:
        ctx: Execution context.

    Returns:
        Set of dangling image IDs.
    """
    proc = ctx.run_command(["docker", "images", "--filter", "dangling=true", "--format", "json"])
    ids: set[str] = set()
    if proc.returncode != 0:
        return ids
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            img = json.loads(line)
            ids.add(img.get("ID", ""))
        except json.JSONDecodeError:
            continue
    return ids


def _images_in_use_by_containers(ctx: Context) -> set[str]:
    """Return image IDs referenced by existing containers.

    Args:
        ctx: Execution context.

    Returns:
        Set of image IDs in use.
    """
    proc = ctx.run_command(["docker", "ps", "--all", "--format", "json"])
    ids: set[str] = set()
    if proc.returncode != 0:
        return ids
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            container = json.loads(line)
            image = container.get("Image", "")
            # Image may be an ID or repo:tag; try inspect to get canonical ID
            img_id = _resolve_image_id(ctx, image)
            if img_id:
                ids.add(img_id)
        except json.JSONDecodeError:
            continue
    return ids


def _resolve_image_id(ctx: Context, image: str) -> str:
    """Resolve a repo:tag or short ID to a canonical image ID.

    Args:
        ctx: Execution context.
        image: Image reference string.

    Returns:
        Canonical image ID, or empty string if resolution fails.
    """
    proc = ctx.run_command(["docker", "inspect", "--format", "{{.Id}}", image])
    if proc.returncode == 0:
        return proc.stdout.strip()
    return ""


def _container_names_for_image(ctx: Context, img_id: str) -> list[str]:
    """Return container names that use a given image ID.

    Args:
        ctx: Execution context.
        img_id: Canonical image ID.

    Returns:
        List of container names.
    """
    proc = ctx.run_command(["docker", "ps", "--all", "--format", "json"])
    names: list[str] = []
    if proc.returncode != 0:
        return names
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            container = json.loads(line)
            c_image = container.get("Image", "")
            c_img_id = _resolve_image_id(ctx, c_image)
            if c_img_id == img_id:
                names.append(container.get("Names", ""))
        except json.JSONDecodeError:
            continue
    return names


def _parse_docker_size(size_str: str) -> int:
    """Parse Docker size strings like '1.23GB' or '456MB'.

    Args:
        size_str: Docker-formatted size string.

    Returns:
        Size in bytes.
    """
    size_str = size_str.strip().replace(" ", "").upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for suffix, mult in sorted(multipliers.items(), key=lambda x: len(x[0]), reverse=True):
        if size_str.endswith(suffix):
            try:
                num = float(size_str[: -len(suffix)])
                return int(num * mult)
            except ValueError:
                return 0
    try:
        return int(float(size_str))
    except ValueError:
        return 0
