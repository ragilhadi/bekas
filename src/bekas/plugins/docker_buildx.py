"""Docker buildx cache plugin for bekas.

Discovers unused Docker BuildKit cache entries and prunes them.
Since buildx cache is fully reproducible, all candidate items are SAFE.
There is no quarantine — cache recreates on demand.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Plugin


class DockerBuildxPlugin(Plugin):
    """Finds stale Docker buildx cache entries.

    Queries ``docker buildx du`` to discover BuildKit cache that has not
    been accessed recently. Cache is fully reproducible, so items are
    classified as ``SAFE`` and pruned without quarantine.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``docker`` must be on PATH.
        supported_platforms: All platforms that Docker supports.
    """

    name = "docker.buildx.cache"
    description = "Finds stale Docker buildx cache entries."
    requires_commands = ["docker"]

    def is_available(self, ctx: Context) -> bool:
        """Check if docker is on PATH.

        Args:
            ctx: Execution context.

        Returns:
            True if the docker command is available.
        """
        return shutil.which("docker") is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield stale buildx cache candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing stale buildx cache entries.
        """
        min_idle_days = ctx.config.get("plugin_settings", {}).get("docker.buildx.cache", {}).get("min_idle_days", 30)
        cutoff = datetime.now(UTC) - timedelta(days=min_idle_days)

        entries = _parse_buildx_du(ctx)
        for entry in entries:
            size_bytes = entry.get("size_bytes", 0)
            last_used = entry.get("last_used_at")
            # Only yield entries older than threshold
            if last_used and last_used > cutoff:
                continue
            id_ = entry.get("id", "unknown")
            yield Candidate(
                id=f"buildx:{id_}",
                category="docker.buildx.cache",
                size_bytes=size_bytes,
                path_or_handle=f"buildx://{id_}",
                confidence=Confidence.SAFE,
                reason=f"BuildKit cache entry idle for {min_idle_days}+ days (reproducible).",
                metadata={
                    "id": id_,
                    "last_used_at": last_used.isoformat() if last_used else None,
                    "size_bytes": size_bytes,
                },
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Prune buildx cache by age filter.

        Args:
            candidate: Buildx cache candidate (metadata drives the filter).
            ctx: Execution context.

        Returns:
            Result of the prune attempt.
        """
        min_idle_days = candidate.metadata.get("min_idle_days", 30)
        proc = ctx.run_command(
            ["docker", "buildx", "prune", "--filter", f"until={min_idle_days}d", "--force"],
            timeout=60,
        )
        success = proc.returncode == 0
        return RemovalResult(
            success=success,
            bytes_freed=candidate.size_bytes if success else 0,
            undo_token=None,
            log=proc.stdout + proc.stderr,
        )

    def supports_quarantine(self) -> bool:
        """Return False because cache cannot be quarantined.

        Returns:
            Whether quarantine is supported.
        """
        return False

    def supports_undo(self) -> bool:
        """Return False because cache cannot be undone.

        Returns:
            Whether undo is supported.
        """
        return False


def _parse_buildx_du(ctx: Context) -> list[dict[str, Any]]:
    """Parse ``docker buildx du --verbose`` output into structured entries.

    Tries JSON first (newer Docker versions), then falls back to parsing
    the plain-text table output.

    Args:
        ctx: Execution context.

    Returns:
        List of dicts with ``id``, ``size_bytes``, and ``last_used_at`` keys.
    """
    # Try JSON first — newer Docker versions support this
    proc = ctx.run_command(["docker", "buildx", "du", "--verbose", "--format", "json"])
    if proc.returncode == 0 and proc.stdout.strip():
        entries: list[dict[str, Any]] = []
        for line in proc.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                size_bytes = _parse_docker_size(entry.get("Size", "0B"))
                last_used = _parse_docker_time(entry.get("LastUsedAt", ""))
                entries.append(
                    {
                        "id": entry.get("ID", "unknown"),
                        "size_bytes": size_bytes,
                        "last_used_at": last_used,
                    }
                )
            except (json.JSONDecodeError, ValueError):
                continue
        return entries

    # Fallback to plain-text table parsing
    proc = ctx.run_command(["docker", "buildx", "du", "--verbose"])
    if proc.returncode != 0 or not proc.stdout.strip():
        return []

    entries = []
    # Look for lines like: "local     1.23GB    2 days ago"
    for line in proc.stdout.strip().splitlines():
        match = re.search(
            r"(?P<type>\S+)\s+(?P<size>\d+\.?\d*\s*[KMGT]?i?B)\s+(?P<age>.+)",
            line,
        )
        if match:
            size_bytes = _parse_docker_size(match.group("size").strip())
            last_used = _parse_human_time(match.group("age").strip())
            entries.append(
                {
                    "id": match.group("type").strip(),
                    "size_bytes": size_bytes,
                    "last_used_at": last_used,
                }
            )
    return entries


def _parse_docker_size(size_str: str) -> int:
    """Parse Docker-formatted size into bytes.

    Args:
        size_str: Docker size string (e.g. ``"1.23GB"``, ``"456MB"``).

    Returns:
        Size in bytes.
    """
    size_str = size_str.strip().replace(" ", "").replace(",", "").upper()
    # Handle both "GB" and "GiB" styles
    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
    }
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


def _parse_docker_time(ts_str: str) -> datetime | None:
    """Parse Docker timestamp string into a datetime.

    Args:
        ts_str: ISO-8601 or Docker-style timestamp.

    Returns:
        Parsed datetime or None if not parsable.
    """
    s = ts_str.strip()
    if not s:
        return None
    # Try ISO-8601 first (common in JSON mode)
    try:
        # Docker truncates to seconds sometimes with trailing Z
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    return _parse_human_time(s)


def _parse_human_time(age_str: str) -> datetime | None:
    """Parse human-readable age like "2 days ago" into a datetime.

    Args:
        age_str: Human-readable duration string.

    Returns:
        Approximate datetime when the entry was last used, or None.
    """
    s = age_str.strip().lower()
    if not s or s == "never":
        return None
    now = datetime.now(UTC)
    # Patterns like "2 days ago", "3 hours ago", "1 week ago"
    m = re.match(r"(?P<num>\d+)\s+(?P<unit>\w+)(?:\s+ago)?", s)
    if m:
        num = int(m.group("num"))
        unit = m.group("unit").lower()
        if unit in ("second", "seconds"):
            return now - timedelta(seconds=num)
        if unit in ("minute", "minutes"):
            return now - timedelta(minutes=num)
        if unit in ("hour", "hours"):
            return now - timedelta(hours=num)
        if unit in ("day", "days"):
            return now - timedelta(days=num)
        if unit in ("week", "weeks"):
            return now - timedelta(weeks=num)
        if unit in ("month", "months"):
            return now - timedelta(days=num * 30)
        if unit in ("year", "years"):
            return now - timedelta(days=num * 365)
    # "Never used" etc.
    if "never" in s or "n/a" in s:
        return None
    return None
