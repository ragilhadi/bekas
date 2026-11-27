"""Docker volumes plugin for bekas.

Discovers named Docker volumes via ``docker volume ls`` and estimates each
volume's size by parsing the per-volume table of ``docker system df -v``.

Named volumes may hold user data (databases, uploads), so every candidate is
``REVIEW``. Removal shells out to ``docker volume rm``; there is no
quarantine because volumes are opaque CLI-managed handles, not movable files.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from typing import Any

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class DockerVolumesPlugin(Plugin):
    """Finds named Docker volumes.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: List of required external commands.
    """

    name = "docker.volumes"
    categories = ("docker.volume",)
    description = "Finds named Docker volumes (may hold user data)."
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
        """Yield named Docker volume candidates.

        Args:
            ctx: Execution context.

        Yields:
            Candidate objects representing Docker volumes.
        """
        try:
            sizes = _volume_sizes(ctx)
            for volume in _list_volumes(ctx):
                name = volume.get("Name", "")
                if not name:
                    continue
                created = volume.get("CreatedAt", "")
                size_bytes = sizes.get(name, 0)
                yield Candidate(
                    id=f"docker-volume:{name}",
                    category="docker.volume",
                    size_bytes=size_bytes,
                    path_or_handle=f"docker://volume/{name}",
                    confidence=Confidence.REVIEW,
                    reason=f"Named Docker volume '{name}'. Volumes may hold user data (databases, uploads).",
                    metadata={"volume_name": name, "created": created, "driver": volume.get("Driver", "")},
                )
        except Exception:
            pass

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Remove a Docker volume.

        Args:
            candidate: Volume candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the removal attempt.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, undo_token=None, log="dry-run")
        volume_name = candidate.metadata.get("volume_name", candidate.id.replace("docker-volume:", ""))
        # Validate volume name: no spaces or shell metacharacters
        if not volume_name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", volume_name):
            return RemovalResult(success=False, bytes_freed=0, log=f"Invalid or unsafe volume name: {volume_name!r}")
        proc = ctx.run_command(["docker", "volume", "rm", volume_name], timeout=60)
        success = proc.returncode == 0
        return RemovalResult(
            success=success,
            bytes_freed=candidate.size_bytes if success else 0,
            undo_token=None,
            log=proc.stdout + proc.stderr,
        )

    def supports_undo(self) -> bool:
        """Return False because Docker volume removal cannot be undone.

        Returns:
            Whether undo is supported.
        """
        return False


def _list_volumes(ctx: Context) -> list[dict[str, Any]]:
    """List named Docker volumes as dictionaries.

    Args:
        ctx: Execution context.

    Returns:
        List of volume metadata dictionaries.
    """
    proc = ctx.run_command(["docker", "volume", "ls", "--format", "json"], timeout=30)
    if proc.returncode != 0:
        return []
    volumes: list[dict[str, Any]] = []
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            volumes.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return volumes


def _volume_sizes(ctx: Context) -> dict[str, int]:
    """Map volume name to size using ``docker system df -v``.

    Args:
        ctx: Execution context.

    Returns:
        Mapping from volume name to size in bytes (missing names get no entry).
    """
    proc = ctx.run_command(["docker", "system", "df", "-v"], timeout=60)
    if proc.returncode != 0:
        return {}
    sizes: dict[str, int] = {}
    in_section = False
    for line in proc.stdout.splitlines():
        header = line.strip()
        if header.startswith("Local Volumes Space usage"):
            in_section = True
            continue
        if not in_section:
            continue
        if not header or header.startswith("-") or header.startswith("VOLUME NAME"):
            continue
        cells = [p.strip() for p in line.split("  ") if p.strip()]
        if len(cells) >= 3:
            # Columns are VOLUME NAME, LINKS, SIZE (extra columns may follow).
            sizes[cells[0]] = _parse_docker_size(cells[-1])
        else:
            # First non-blank line after the blank separator: next section
            in_section = False
    return sizes


def _parse_docker_size(size_str: str) -> int:
    """Parse Docker size strings like '1.23GB' or '456MB'.

    Args:
        size_str: Docker-formatted size string.

    Returns:
        Size in bytes, 0 when unparseable.
    """
    size_str = size_str.strip().replace(" ", "").upper()
    m = re.fullmatch(r"([0-9.]+)([KMGTP]?B?)", size_str)
    if not m or not m.group(1):
        return 0
    try:
        value = float(m.group(1))
    except ValueError:
        return 0
    unit = m.group(2).replace("B", "")
    multiplier = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}.get(unit, 1)
    return int(value * multiplier)
