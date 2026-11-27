"""Snap and Flatpak cleanup plugin for bekas.

Discovers two categories:

- ``snap.revs``: old disabled snap revisions from ``snap list --all``. Snaps
  keep previous revisions for rollback; disabled ones can be removed with
  ``snap remove <name> --revision=<r>``.
- ``flatpak.unused``: unused runtimes from ``flatpak list --unused``. Removal
  uninstalls each explicit ref discovered at scan time (metadata carries the
  exact ref); no blanket ``flatpak uninstall --unused -y`` sweep is run, so
  concurrent installs cannot widen deletion beyond what the user approved.

Both may hold app data, so all candidates are ``REVIEW``.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin

_TIMEOUT_SHORT = 30
_TIMEOUT_LONG = 90


class SnapFlatpakPlugin(Plugin):
    """Finds old snap revisions and unused flatpak runtimes.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: List of required external commands.
    """

    name = "linux.snap_flatpak"
    categories = ("snap.revs", "flatpak.unused")
    description = "Finds disabled snap revisions and unused flatpak runtimes."
    requires_commands = ["snap", "flatpak"]
    capabilities = Capabilities(requires_cli=("snap", "flatpak"), quarantine=False, estimated_runtime="medium")

    def is_available(self, ctx: Context) -> bool:
        """Check if the ``snap`` and ``flatpak`` commands are on PATH.

        Args:
            ctx: Execution context.

        Returns:
            True if both package managers are available.
        """
        return shutil.which("snap") is not None and shutil.which("flatpak") is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield snap revision and flatpak unused-runtime candidates.

        Args:
            ctx: Execution context.

        Yields:
            Candidate objects for snap revisions and flatpak unused refs.
        """
        for discovered in (_discover_snap_revisions, _discover_flatpak_unused):
            try:
                yield from discovered(ctx)
            except Exception:
                if ctx.verbose:
                    print(f"linux.snap_flatpak: {discovered.__name__} failed")

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Remove a snap revision or one explicit flatpak ref.

        Args:
            candidate: Candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the removal attempt.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, undo_token=None, log="dry-run")
        if candidate.category == "snap.revs":
            return _remove_snap_revision(candidate, ctx)
        return _remove_flatpak_ref(candidate, ctx)

    def supports_undo(self) -> bool:
        """Return False because snap/flatpak removal cannot be undone.

        Returns:
            Whether undo is supported.
        """
        return False


def _discover_snap_revisions(ctx: Context) -> Iterator[Candidate]:
    """Yield candidates for disabled (old) snap revisions.

    Args:
        ctx: Execution context.

    Yields:
        Candidate objects, one per disabled revision.
    """
    proc = ctx.run_command(["snap", "list", "--all"], timeout=_TIMEOUT_SHORT)
    if proc.returncode != 0:
        return
    for line in proc.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("Name"):
            continue
        cells = [p.strip() for p in line.split("  ") if p.strip()]
        if len(cells) < 4:
            continue
        name, revision, tracking = cells[0], cells[2], cells[3]
        notes = cells[-1].lower() if len(cells) >= 5 else ""
        if "disabled" not in notes:
            continue
        if not _SAFE_SNAP_TOKEN.fullmatch(revision):
            continue
        # `snap list --all` has no size column; freed bytes are unknown.
        size_bytes = 0
        yield Candidate(
            id=f"snap-rev:{name}/{revision}",
            category="snap.revs",
            size_bytes=size_bytes,
            path_or_handle=f"snap://revision/{name}/{revision}",
            confidence=Confidence.REVIEW,
            reason=f"Disabled snap revision {revision} of '{name}' (tracking {tracking}); superseded by a newer rev.",
            metadata={"snap": name, "revision": revision, "tracking": tracking, "notes": notes},
        )


def _discover_flatpak_unused(ctx: Context) -> Iterator[Candidate]:
    """Yield candidates for unused flatpak runtimes/extensions.

    Args:
        ctx: Execution context.

    Yields:
        Candidate objects, one per unused ref.
    """
    proc = ctx.run_command(["flatpak", "list", "--unused", "--columns=application,ref,size"], timeout=_TIMEOUT_SHORT)
    if proc.returncode != 0:
        return
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("application"):
            continue
        parts = [p.strip() for p in re.split(r"\s\s+", line) if p.strip()]
        if len(parts) >= 3:
            application, ref, size_str = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            application, ref, size_str = parts[0], parts[1], ""
        else:
            continue
        if ref in seen or not _SAFE_REF.fullmatch(ref):
            continue
        seen.add(ref)
        yield Candidate(
            id=f"flatpak-unused:{ref}",
            category="flatpak.unused",
            size_bytes=_parse_flatpak_size(size_str),
            path_or_handle=f"flatpak://ref/{ref}",
            confidence=Confidence.REVIEW,
            reason=f"Unused flatpak runtime/extension '{application}' ({ref}); not required by any installed app.",
            metadata={"ref": ref, "application": application},
        )


def _remove_snap_revision(candidate: Candidate, ctx: Context) -> RemovalResult:
    """Run ``snap remove <name> --revision=<r>`` for one candidate.

    Args:
        candidate: A ``snap.revs`` candidate.
        ctx: Execution context.

    Returns:
        Result of the removal attempt.
    """
    name = str(candidate.metadata.get("snap", ""))
    revision = str(candidate.metadata.get("revision", ""))
    if not _SAFE_SNAP_TOKEN.fullmatch(name) or not _SAFE_SNAP_TOKEN.fullmatch(revision):
        return RemovalResult(success=False, bytes_freed=0, log=f"Invalid snap name/revision: {name!r}/{revision!r}")
    proc = ctx.run_command(["snap", "remove", name, f"--revision={revision}"], timeout=_TIMEOUT_LONG)
    success = proc.returncode == 0
    return RemovalResult(
        success=success,
        bytes_freed=candidate.size_bytes if success else 0,
        undo_token=None,
        log=proc.stdout + proc.stderr,
    )


def _remove_flatpak_ref(candidate: Candidate, ctx: Context) -> RemovalResult:
    """Uninstall one explicit flatpak ref recorded at scan time.

    Args:
        candidate: A ``flatpak.unused`` candidate.
        ctx: Execution context.

    Returns:
        Result of the removal attempt.
    """
    ref = str(candidate.metadata.get("ref", ""))
    if not _SAFE_REF.fullmatch(ref) or ".." in ref.split("/"):
        return RemovalResult(success=False, bytes_freed=0, log=f"Invalid flatpak ref: {ref!r}")
    proc = ctx.run_command(["flatpak", "uninstall", "--noninteractive", ref], timeout=_TIMEOUT_LONG)
    success = proc.returncode == 0
    return RemovalResult(
        success=success,
        bytes_freed=candidate.size_bytes if success else 0,
        undo_token=None,
        log=proc.stdout + proc.stderr,
    )


_SAFE_SNAP_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")
_SAFE_REF = re.compile(r"[A-Za-z0-9/_.\-]+")


def _parse_snap_size(size_str: str) -> int:
    """Parse snap size strings like '12.34M' or '566.2M'.

    Args:
        size_str: Snap-formatted size string.

    Returns:
        Size in bytes, 0 when empty or unparseable.
    """
    size_str = size_str.strip().upper()
    if not size_str:
        return 0
    m = re.fullmatch(r"([0-9.]+)([KMGTP]?B?)", size_str)
    if not m:
        return 0
    try:
        value = float(m.group(1))
    except ValueError:
        return 0
    unit = m.group(2).replace("B", "")
    multiplier = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}.get(unit, 1)
    return int(value * multiplier)


def _parse_flatpak_size(size_str: str) -> int:
    """Parse flatpak size strings like '1.2 GB' or '450.5 MB'.

    Args:
        size_str: Flatpak-formatted size string.

    Returns:
        Size in bytes, 0 when empty or unparseable.
    """
    size_str = size_str.replace(" ", "").upper()
    if not size_str:
        return 0
    m = re.fullmatch(r"([0-9.]+)(K|M|G|T)?B?", size_str)
    if not m:
        return 0
    try:
        value = float(m.group(1))
    except ValueError:
        return 0
    multiplier = {"": 1, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4}.get(m.group(2) or "", 1)
    return int(value * multiplier)
