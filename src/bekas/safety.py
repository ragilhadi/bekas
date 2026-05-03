"""Safety model and hard exclusions.

This module defines the safety boundary for bekas. Every candidate discovered
by any plugin must pass through ``filter_candidates`` (which delegates to
``is_excluded``) before it is ever shown to the user or acted upon.

Hard exclusions (system paths, sensitive directories) and user-defined
exclusion patterns combine to create a multi-layer defense.

Exclusion layers (checked in order, short-circuit on first match):

1. **Path traversal** — raw string match for ``..`` as a path component.
2. **Hard system paths** — ``/bin``, ``/usr``, ``/etc``, ``/System``, etc.
3. **Sensitive globs** — ``.ssh``, ``.gnupg``, ``.aws``, ``*.key``, etc.
4. **Symlink outside $HOME** — symlinks that escape the home directory.
5. **Non-local filesystem** — NFS, SMB, FUSE mounts (via ``psutil``).
6. **Recently modified** — files touched within ``min_quiet_hours`` (default 6).
7. **Active virtual environment** — paths inside ``$VIRTUAL_ENV`` or ``$CONDA_PREFIX``.
8. **User-defined patterns** — globs and exact paths from config.

See ``docs/SAFETY.md`` for the full documented exclusion list and rationale.
"""

from __future__ import annotations

import fnmatch
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from bekas.models import Candidate

# Hard exclusions: these paths are NEVER touched by any plugin.
_HARD_EXCLUSIONS: list[str] = [
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/etc",
    "/var/db",
    "/var/lib",
    "/var/log",
    "/var/run",
    "/var/spool",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    # macOS system paths
    "/System",
    "/Library",
    # Windows system paths
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\Users\All Users",
    r"C:\Users\Default",
    r"C:\Users\Public",
]

_SENSITIVE_GLOBS: list[str] = [
    "**/.ssh/*",
    "**/.gnupg/*",
    "**/.aws/*",
    "**/.kube/*",
    "**/.config/git/*",
    "**/.netrc",
    "**/*.key",
    "**/*.pem",
    "**/*.gpg",
    "**/.git",
    "**/.git/**",
]

_SENSITIVE_NAMES: set[str] = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".netrc",
    ".git",
}

# Path traversal pattern: matches .. as a standalone path component
_TRAVERSAL_RE = re.compile(r"(?:^|/|\\)\.\.(?:/|\\|$)")

# Non-local filesystem types we refuse to touch
_NON_LOCAL_FSTYPES: set[str] = {"nfs", "nfs4", "cifs", "smbfs", "fuse", "fuse.sshfs"}


def _expand_exclusions() -> list[Path]:
    """Build a list of resolved paths that must never be touched.

    Includes hard system paths, home-level sensitive directories,
    and Windows environment paths when applicable.

    Returns:
        Resolved Path objects representing excluded locations.
    """
    excl: list[Path] = []
    for p in _HARD_EXCLUSIONS:
        try:
            excl.append(Path(p).resolve())
        except (OSError, ValueError):
            continue
    # Add home-level exclusions
    try:
        home = Path.home().resolve()
        for name in _SENSITIVE_NAMES:
            excl.append(home / name)
        # Also exclude Windows %LOCALAPPDATA%, %APPDATA%, %TEMP% if applicable
        for env in ("LOCALAPPDATA", "APPDATA", "TEMP"):
            val = os.environ.get(env)
            if val:
                try:
                    excl.append(Path(val).resolve())
                except (OSError, ValueError):
                    continue
    except RuntimeError:
        pass
    return excl


def _is_ancestor(ancestor: Path, child: Path) -> bool:
    """Return True if ancestor is a strict ancestor of child (and not root).

    Args:
        ancestor: Potential ancestor path.
        child: Path to test.

    Returns:
        True if child is inside ancestor but not equal to it.
    """
    try:
        child.relative_to(ancestor)
        return ancestor != child
    except ValueError:
        return False


def _is_symlink_outside_home(path: Path) -> bool:
    """Return True if path is a symlink that points outside the home directory.

    Args:
        path: Path to evaluate.

    Returns:
        True if the symlink target is outside ``$HOME``.
    """
    try:
        if not path.is_symlink():
            return False
        home = Path.home().resolve()
        target = path.resolve()
        try:
            target.relative_to(home)
            return False
        except ValueError:
            return True
    except (OSError, RuntimeError):
        return True


def _is_non_local_filesystem(path: Path | str) -> bool:
    """Return True if path resides on a non-local filesystem.

    Detects NFS, CIFS/SMB, FUSE, and other remote mounts via ``psutil``.

    Args:
        path: Path to evaluate.

    Returns:
        True if the filesystem is non-local.
    """
    try:
        import psutil

        resolved = Path(path).resolve()
        partitions = psutil.disk_partitions(all=True)
        for part in partitions:
            if part.fstype.lower() in _NON_LOCAL_FSTYPES:
                try:
                    resolved.relative_to(Path(part.mountpoint).resolve())
                    return True
                except ValueError:
                    continue
    except (ImportError, OSError, RuntimeError):
        pass
    return False


def _is_too_recent(path: Path, min_quiet_hours: int = 6) -> bool:
    """Return True if the file or directory was modified too recently.

    Args:
        path: Path to evaluate.
        min_quiet_hours: Minimum hours since last modification for the item
            to be considered safe to touch. Defaults to 6.

    Returns:
        True if the mtime is newer than the threshold.
    """
    try:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        age = datetime.now(UTC) - mtime
        return age < __import__("datetime").timedelta(hours=min_quiet_hours)
    except (OSError, ValueError):
        return False


def _is_inside_active_venv(path: Path) -> bool:
    """Return True if path is inside the active virtual environment.

    Args:
        path: Path to evaluate.

    Returns:
        True if ``$VIRTUAL_ENV`` or ``$CONDA_PREFIX`` is set and the path
        is inside it.
    """
    for env_var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        venv = os.environ.get(env_var)
        if venv:
            try:
                venv_path = Path(venv).resolve()
                if path.resolve() == venv_path or _is_ancestor(venv_path, path.resolve()):
                    return True
            except (OSError, RuntimeError):
                continue
    return False


def is_excluded(
    path: Path | str,
    user_exclusions: list[str] | None = None,
    min_quiet_hours: int = 0,
) -> bool:
    """Determine whether a path must never be touched.

    Checks traversal patterns, hard system exclusions, sensitive globs,
    symlink safety, filesystem locality, modification recency, active
    virtual environments, and user-defined exclusion patterns.

    Args:
        path: Filesystem path or string to evaluate.
        user_exclusions: Optional list of user-defined exclusion patterns.
        min_quiet_hours: Minimum hours since last modification. Defaults
            to 6.

    Returns:
        True if the path is excluded, False otherwise.
    """
    raw_str = str(path)

    # Path traversal guard: check BEFORE resolving, on the raw path string.
    if _TRAVERSAL_RE.search(raw_str):
        return True

    # Resolve the path, treating resolution errors as excluded for safety.
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        return True

    # Hard system exclusions (resolved and checked canonically)
    for ex in _expand_exclusions():
        try:
            if resolved == ex or _is_ancestor(ex, resolved):
                return True
        except (OSError, ValueError, RuntimeError):
            return True

    # Sensitive globs (check against resolved path)
    sp = str(resolved)
    for pattern in _SENSITIVE_GLOBS:
        if fnmatch.fnmatch(sp, pattern):
            return True
        # Also allow matching just the filename for simple patterns like *.key
        if "/" not in pattern and fnmatch.fnmatch(resolved.name, pattern):
            return True

    # Symlink pointing outside home
    if _is_symlink_outside_home(resolved):
        return True

    # Non-local filesystem
    if _is_non_local_filesystem(resolved):
        return True

    # Recently modified
    if _is_too_recent(resolved, min_quiet_hours):
        return True

    # Active virtual environment
    if _is_inside_active_venv(resolved):
        return True

    # User-defined exclusions
    if user_exclusions:
        home = str(Path.home())
        for raw in user_exclusions:
            pat = raw.replace("~/", home + "/")
            # Support simple **/ glob
            if "**" in pat:
                parts = pat.replace("**", "").strip("/").split("/")
                # crude containment match
                if all(part in sp for part in parts if part):
                    return True
            else:
                if fnmatch.fnmatch(sp, pat) or fnmatch.fnmatch(sp, pat + "/*"):
                    return True
            # Exact directory match
            try:
                rp = Path(pat).resolve()
                if resolved == rp or _is_ancestor(rp, resolved):
                    return True
            except (OSError, ValueError):
                continue

    return False


def is_safe_to_delete(
    path: Path | str,
    user_exclusions: list[str] | None = None,
    min_quiet_hours: int = 0,
) -> tuple[bool, str]:
    """Return whether a path is safe to delete and a human-readable reason.

    This is the **recommended** safety API for plugins that need to explain
    why a candidate was excluded. It returns ``(True, "")`` when the path is
    safe, and ``(False, "reason")`` when any exclusion layer matches.

    Args:
        path: Filesystem path or string to evaluate.
        user_exclusions: Optional list of user-defined exclusion patterns.
        min_quiet_hours: Minimum hours since last modification. Defaults
            to 6.

    Returns:
        Tuple of ``(is_safe, reason)``. ``is_safe`` is True when the path
        passes all exclusion checks. ``reason`` is an empty string when safe,
        otherwise a short human-readable explanation.
    """
    raw_str = str(path)
    resolved: Path
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        return (False, "unable to resolve path")

    # Check each layer individually so we can return a specific reason.
    if _TRAVERSAL_RE.search(raw_str):
        return (False, "path traversal pattern detected")

    for ex in _expand_exclusions():
        try:
            if resolved == ex or _is_ancestor(ex, resolved):
                return (False, f"hard-excluded system path: {ex}")
        except (OSError, ValueError, RuntimeError):
            return (False, "error checking system exclusions")

    sp = str(resolved)
    for pattern in _SENSITIVE_GLOBS:
        if fnmatch.fnmatch(sp, pattern):
            return (False, f"sensitive glob match: {pattern}")
        if "/" not in pattern and fnmatch.fnmatch(resolved.name, pattern):
            return (False, f"sensitive name match: {pattern}")

    if _is_symlink_outside_home(resolved):
        return (False, "symlink points outside home directory")

    if _is_non_local_filesystem(resolved):
        return (False, "non-local filesystem (NFS/SMB/FUSE)")

    if _is_too_recent(resolved, min_quiet_hours):
        return (False, f"modified within last {min_quiet_hours} hours")

    if _is_inside_active_venv(resolved):
        return (False, "inside active virtual environment")

    if user_exclusions:
        home = str(Path.home())
        for raw in user_exclusions:
            pat = raw.replace("~/", home + "/")
            if "**" in pat:
                parts = pat.replace("**", "").strip("/").split("/")
                if all(part in sp for part in parts if part):
                    return (False, f"user exclusion: {raw}")
            else:
                if fnmatch.fnmatch(sp, pat) or fnmatch.fnmatch(sp, pat + "/*"):
                    return (False, f"user exclusion: {raw}")
            try:
                rp = Path(pat).resolve()
                if resolved == rp or _is_ancestor(rp, resolved):
                    return (False, f"user exclusion: {raw}")
            except (OSError, ValueError):
                continue

    return (True, "")


def filter_candidates(
    candidates: list[Candidate],
    user_exclusions: list[str] | None = None,
    min_quiet_hours: int = 0,
) -> list[Candidate]:
    """Remove any candidates whose path_or_handle is excluded.

    Args:
        candidates: List of candidates to filter.
        user_exclusions: Optional user-defined exclusion patterns.
        min_quiet_hours: Minimum hours since last modification. Defaults
            to 6.

    Returns:
        Candidates whose paths are not excluded.
    """
    safe: list[Candidate] = []
    for c in candidates:
        if is_excluded(c.path_or_handle, user_exclusions, min_quiet_hours):
            continue
        safe.append(c)
    return safe
