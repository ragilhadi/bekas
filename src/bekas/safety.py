"""Safety model and hard exclusions.

This module defines the safety boundary for bekas. Every candidate discovered
by any plugin must pass through ``filter_candidates`` (which delegates to
``is_excluded``) before it is ever shown to the user or acted upon.

Hard exclusions (system paths, sensitive directories) and user-defined
exclusion patterns combine to create a multi-layer defense.

Exclusion layers (checked in order, short-circuit on first match):

1. **Path traversal** — raw string match for ``..`` as a path component.
2. **Hard system paths** — ``/bin``, ``/usr``, ``/etc``, ``/System``, etc.,
   including the system roots themselves (``/``, ``/home``, ``$HOME``) and any
   path that *contains* a protected location.
3. **Sensitive globs** — ``.ssh``, ``.gnupg``, ``.aws``, ``*.key``, etc.
4. **Symlink outside $HOME** — symlinks that escape the home directory.
5. **Non-local filesystem** — NFS, SMB, FUSE mounts (via ``psutil``).
6. **Recently modified** — files touched within ``min_quiet_hours`` (default 6).
7. **Active virtual environment** — paths inside ``$VIRTUAL_ENV`` or ``$CONDA_PREFIX``.
8. **User-defined patterns** — globs and exact paths from config.

See ``docs/SAFETY.md`` for the full documented exclusion list and rationale.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from bekas.models import Candidate

# Hard exclusions: these paths are NEVER touched by any plugin.
_HARD_EXCLUSIONS: list[str] = [
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
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

# Paths that are protected only as themselves: their children are legitimate
# cleaning targets (``/tmp/x``, ``/home/me/.cache``), but the directory as a
# whole must never be a candidate. ``$HOME`` is added at runtime.
_PROTECTED_EXACT: list[str] = [
    "/",
    "/home",
    "/Users",
    "/root",
    "/opt",
    "/srv",
    "/mnt",
    "/media",
    "/tmp",
    "/var/tmp",
]

_SENSITIVE_GLOBS: list[str] = [
    "**/.ssh",
    "**/.ssh/*",
    "**/.gnupg",
    "**/.gnupg/*",
    "**/.aws",
    "**/.aws/*",
    "**/.kube",
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
    # Add home-level exclusions. Note: %TEMP%, %LOCALAPPDATA%, and %APPDATA%
    # are deliberately NOT excluded — bekas's own cleaning targets (browser
    # caches, temp files, IDE caches) live there, mirroring how /tmp and
    # ~/.cache are not hard-excluded on POSIX. Specific sensitive paths are
    # still protected by _SENSITIVE_GLOBS and the system paths above.
    try:
        home = Path.home().resolve()
        for name in _SENSITIVE_NAMES:
            excl.append(home / name)
    except RuntimeError:
        pass
    # bekas's own data directory (quarantine, undo DB, lock, audit log) must
    # never be rediscovered as a cleaning candidate, otherwise quarantined
    # items could be permanently deleted and could no longer be restored.
    try:
        from bekas.config import _data_dir

        excl.append(_data_dir().resolve())
    except (OSError, ValueError, RuntimeError):
        pass
    return excl


def _expand_exact_exclusions() -> list[Path]:
    """Build the list of paths protected only as themselves (not their children).

    Returns:
        Resolved paths that must never be a candidate, including ``$HOME``.
    """
    exact: list[Path] = []
    for p in _PROTECTED_EXACT:
        try:
            exact.append(Path(p).resolve())
        except (OSError, ValueError):
            continue
    with contextlib.suppress(OSError, RuntimeError):
        exact.append(Path.home().resolve())
    return exact


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
    if min_quiet_hours <= 0:
        return False
    try:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        age = datetime.now(UTC) - mtime
        return age < timedelta(hours=min_quiet_hours)
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


def _glob_targets(raw: str, resolved: Path) -> list[str]:
    """Return the strings a glob pattern should be matched against.

    Both the resolved path and the caller-supplied raw path are candidates,
    posix-normalized: on macOS ``/tmp`` resolves to ``/private/tmp`` and on
    Windows separators differ, so matching only the resolved form silently
    breaks patterns written against the path the user actually sees.

    Args:
        raw: The path string as originally supplied.
        resolved: The fully resolved path.

    Returns:
        Deduplicated list of strings to match patterns against.
    """
    return list({resolved.as_posix(), Path(raw).as_posix()})


def _sensitive_glob_match(raw: str, resolved: Path) -> str | None:
    """Return the first sensitive glob matching the path, if any.

    Args:
        raw: The path string as originally supplied.
        resolved: The fully resolved path.

    Returns:
        The matching pattern, or None.
    """
    targets = _glob_targets(raw, resolved)
    for pattern in _SENSITIVE_GLOBS:
        if any(fnmatch.fnmatch(t, pattern) for t in targets):
            return pattern
        # Also allow matching just the filename for simple patterns like *.key
        if "/" not in pattern and fnmatch.fnmatch(resolved.name, pattern):
            return pattern
    return None


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into a compiled regular expression.

    ``*`` and ``?`` never cross a ``/``; ``**`` does. A pattern with no ``/``
    (``node_modules``, ``*.bak``) or a relative one (``foo/bar``) may match at
    any depth; an absolute pattern is anchored at the filesystem root.

    Args:
        pattern: Glob using ``/`` separators.

    Returns:
        Compiled expression to be used with ``fullmatch``.
    """
    pat = pattern.rstrip("/") or "/"
    out: list[str] = []
    if not pat.startswith("/") and not re.match(r"^[A-Za-z]:/", pat):
        out.append("(?:.*/)?")
    i = 0
    while i < len(pat):
        ch = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[" and (end := pat.find("]", i + 2)) != -1:
            body = pat[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("".join(out), re.IGNORECASE if sys.platform == "win32" else 0)


def _normalise_user_pattern(raw_pat: str) -> str:
    """Expand a leading ``~`` and normalise separators of a user pattern.

    Args:
        raw_pat: Pattern exactly as written in the config.

    Returns:
        Pattern with ``/`` separators and ``~`` expanded only at the start.
    """
    pat = raw_pat.strip()
    if sys.platform == "win32":
        pat = pat.replace("\\", "/")
    if pat == "~" or pat.startswith(("~/", "~\\")):
        pat = Path.home().as_posix() + pat[1:].replace("\\", "/")
    return pat


def _path_and_ancestors(target: str) -> list[str]:
    """Return a posix path followed by each of its parents.

    Args:
        target: Posix-style path string.

    Returns:
        ``[target, parent, grandparent, ...]``.
    """
    pp = PurePosixPath(target)
    return [str(pp), *(str(a) for a in pp.parents)]


def _user_exclusion_match(raw: str, resolved: Path, user_exclusions: list[str]) -> str | None:
    """Return the first user exclusion pattern matching the path, if any.

    Patterns follow gitignore-style rules (see :func:`_glob_to_regex`). A match
    on the path or on any of its parent directories excludes it, so excluding
    a directory also excludes everything inside.

    Args:
        raw: The path string as originally supplied.
        resolved: The fully resolved path.
        user_exclusions: User-defined exclusion patterns (globs or paths).

    Returns:
        The matching pattern as the user wrote it, or None.
    """
    targets = [c for t in _glob_targets(raw, resolved) for c in _path_and_ancestors(t)]
    for raw_pat in user_exclusions:
        pat = _normalise_user_pattern(raw_pat)
        if not pat:
            continue
        regex = _glob_to_regex(pat)
        if any(regex.fullmatch(t) for t in targets):
            return raw_pat
        # Plain paths are also compared after resolving, so a pattern naming a
        # symlinked directory still excludes what lives behind it.
        if not any(c in pat for c in "*?["):
            try:
                rp = Path(pat).resolve()
                if resolved == rp or _is_ancestor(rp, resolved):
                    return raw_pat
            except (OSError, ValueError):
                continue
    return None


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
            to 0 (no recency check).

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
            if _is_ancestor(resolved, ex):
                return (False, f"contains protected path: {ex}")
        except (OSError, ValueError, RuntimeError):
            return (False, "error checking system exclusions")

    for ex in _expand_exact_exclusions():
        if resolved == ex:
            return (False, f"protected location: {ex}")

    sensitive = _sensitive_glob_match(raw_str, resolved)
    if sensitive is not None:
        return (False, f"sensitive glob match: {sensitive}")

    # Must look at the path as supplied: ``resolved`` has already followed any
    # symlink, so it can never be reported as one.
    if _is_symlink_outside_home(Path(path)):
        return (False, "symlink points outside home directory")

    if _is_non_local_filesystem(resolved):
        return (False, "non-local filesystem (NFS/SMB/FUSE)")

    if _is_too_recent(resolved, min_quiet_hours):
        return (False, f"modified within last {min_quiet_hours} hours")

    if _is_inside_active_venv(resolved):
        return (False, "inside active virtual environment")

    if user_exclusions:
        matched = _user_exclusion_match(raw_str, resolved, user_exclusions)
        if matched is not None:
            return (False, f"user exclusion: {matched}")

    return (True, "")


def is_excluded(
    path: Path | str,
    user_exclusions: list[str] | None = None,
    min_quiet_hours: int = 0,
) -> bool:
    """Determine whether a path must never be touched.

    Thin wrapper over :func:`is_safe_to_delete`, so both APIs always agree.

    Args:
        path: Filesystem path or string to evaluate.
        user_exclusions: Optional list of user-defined exclusion patterns.
        min_quiet_hours: Minimum hours since last modification. Defaults
            to 0 (no recency check).

    Returns:
        True if the path is excluded, False otherwise.
    """
    return not is_safe_to_delete(path, user_exclusions, min_quiet_hours)[0]


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
            to 0 (no recency check).

    Returns:
        Candidates whose paths are not excluded.
    """
    safe: list[Candidate] = []
    for c in candidates:
        if is_excluded(c.path_or_handle, user_exclusions, min_quiet_hours):
            continue
        safe.append(c)
    return safe
