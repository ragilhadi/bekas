"""Safety model and hard exclusions."""

from __future__ import annotations

import fnmatch
import os
import re
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
]

_SENSITIVE_NAMES: set[str] = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".netrc",
}

# Path traversal pattern: matches .. followed by / or \ anywhere in the raw path string
_TRAVERSAL_RE = re.compile(r"\.\.[/\\]|/\.\./|\\\.\\")


def _expand_exclusions() -> list[Path]:
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
    """Return True if ancestor is a strict ancestor of child (and not root)."""
    try:
        # Use relative_to for robust ancestor checking
        child.relative_to(ancestor)
        return ancestor != child
    except ValueError:
        return False


def is_excluded(path: Path | str, user_exclusions: list[str] | None = None) -> bool:
    """Return True if the given path must never be touched."""
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
                return True

    return False


def filter_candidates(candidates: list[Candidate], user_exclusions: list[str] | None = None) -> list[Candidate]:
    """Remove any candidates whose path_or_handle is excluded."""
    safe: list[Candidate] = []
    for c in candidates:
        if is_excluded(c.path_or_handle, user_exclusions):
            continue
        safe.append(c)
    return safe
