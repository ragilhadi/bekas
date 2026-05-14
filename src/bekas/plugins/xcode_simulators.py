"""Xcode simulators plugin for bekas (macOS only).

Discovers abandoned Xcode simulator runtimes after Xcode upgrades.
Typically reclaims 5–20 GB.

Uses ``xcrun simctl list runtimes --json`` to find runtimes whose
availability is ``unavailable`` or ``bundlePath`` points to a non-
existent Xcode.  Removal re-downloads at need; undo is not supported.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from collections.abc import Iterator

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class XcodeSimulatorsPlugin(Plugin):
    """Finds abandoned Xcode simulator runtimes.

    Uses ``xcrun simctl list runtimes --json`` to identify runtimes
    that are no longer available on the current Xcode version.
    These are left behind after Xcode upgrades and can safely be
    removed.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``xcrun`` required.
    """

    name = "xcode.simulators"
    description = "Finds abandoned Xcode simulator runtimes (macOS only)."
    requires_commands = ["xcrun"]
    capabilities = Capabilities(
        quarantine=False,
        estimated_runtime="medium",
        platforms=("darwin",),
        requires_cli=("xcrun",),
    )

    def is_available(self, ctx: Context) -> bool:
        """Check if on macOS and ``xcrun`` is available.

        Args:
            ctx: Execution context.

        Returns:
            True if on macOS and xcrun is on PATH.
        """
        if platform.system().lower() != "darwin":
            return False
        return shutil.which("xcrun") is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield abandoned Xcode simulator runtime candidates.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing abandoned simulator runtimes.
        """
        runtimes = _list_unavailable_runtimes()
        if not runtimes:
            return

        # Each unavailable runtime gets its own candidate so removal
        # can be granular and reported accurately.
        for runtime in runtimes:
            runtime_id = runtime["identifier"]
            size = runtime.get("size_bytes", _runtime_size_estimate(runtime_id))
            name = runtime.get("name", runtime_id)
            yield Candidate(
                id=f"xcode:{runtime_id}",
                category="xcode.simulators",
                size_bytes=size,
                path_or_handle=runtime_id,
                confidence=Confidence.SAFE,
                reason=f"Abandoned Xcode simulator runtime: {name}",
                metadata={
                    "runtime_id": runtime_id,
                    "runtime_name": name,
                    "bundle_path": runtime.get("bundlePath", ""),
                    "version": runtime.get("version", ""),
                },
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete an unavailable Xcode simulator runtime.

        Args:
            candidate: Simulator runtime candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, log="dry-run")
        runtime_id = candidate.path_or_handle
        try:
            result = subprocess.run(
                ["xcrun", "simctl", "runtime", "delete", runtime_id],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return RemovalResult(
                    success=True,
                    bytes_freed=candidate.size_bytes,
                    log=f"Deleted runtime {runtime_id}",
                )
            return RemovalResult(
                success=False,
                bytes_freed=0,
                log=f"xcrun failed: {result.stderr}",
            )
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return False because simulator runtimes cannot be undone.

        Returns:
            False.
        """
        return False


def _list_unavailable_runtimes() -> list[dict]:
    """List unavailable Xcode simulator runtime dicts.

    Uses ``xcrun simctl list runtimes --json`` to find runtimes whose
    ``availability`` field is ``unavailable`` or whose ``bundlePath`` no
    longer exists.

    Returns:
        List of runtime dicts with keys: identifier, name, version,
        bundlePath, availability, size_bytes (if reported).
    """
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "runtimes", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        runtimes: list[dict] = []
        # JSON shape: {"runtimes": [{"identifier": "...", "name": "...", ...}, ...]}
        for runtime in data.get("runtimes", []):
            availability = runtime.get("availability", "").lower()
            bundle_path = runtime.get("bundlePath", "")
            is_unavailable = (
                availability == "unavailable"
                or (bundle_path and not _path_exists(bundle_path))
            )
            if is_unavailable:
                runtimes.append(runtime)
        return runtimes
    except Exception:
        return []


def _path_exists(path_str: str) -> bool:
    """Check if a path string exists.

    Args:
        path_str: Path to check.

    Returns:
        True if the path exists.
    """
    from pathlib import Path
    return Path(path_str).exists()


def _runtime_size_estimate(runtime_id: str) -> int:
    """Estimate the size of a simulator runtime.

    Args:
        runtime_id: Simulator runtime identifier.

    Returns:
        Estimated size in bytes (conservative guess of 5 GB per runtime).
    """
    _ = runtime_id
    return 5 * 1024 * 1024 * 1024
