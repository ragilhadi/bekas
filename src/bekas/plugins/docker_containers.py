"""Docker containers plugin for bekas."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class DockerContainersPlugin(Plugin):
    """Finds stopped Docker containers older than N days."""

    name = "docker.containers"
    description = "Finds stopped Docker containers older than a threshold."
    requires_commands = ["docker"]

    def is_available(self, ctx: Context) -> bool:
        return shutil.which("docker") is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        try:
            proc = ctx.run_command(["docker", "ps", "--filter", "status=exited", "--format", "json"])
            if proc.returncode != 0:
                return
            for line in proc.stdout.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    container = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = container.get("ID", "")
                name = container.get("Names", "")
                image = container.get("Image", "")
                created = container.get("CreatedAt", "")
                # Stopped containers are safe to remove
                yield Candidate(
                    id=f"docker-container:{cid}",
                    category="docker.container.stopped",
                    size_bytes=0,
                    path_or_handle=f"docker-container://{cid}",
                    confidence=Confidence.SAFE,
                    reason=f"Stopped container '{name}'.",
                    metadata={"container_id": cid, "name": name, "image": image, "created": created},
                )
        except Exception:
            pass

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        cid = candidate.metadata.get("container_id", candidate.id.replace("docker-container:", ""))
        proc = ctx.run_command(["docker", "rm", cid])
        success = proc.returncode == 0
        return RemovalResult(
            success=success,
            bytes_freed=0,
            undo_token=None,
            log=proc.stdout + proc.stderr,
        )

    def supports_undo(self) -> bool:
        return False

    def supports_quarantine(self) -> bool:
        return False
