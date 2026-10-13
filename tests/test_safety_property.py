"""Property-based tests for the safety boundary (using ``hypothesis``).

Generates arbitrary paths and verifies that no system path ever passes
``is_safe_to_delete`` — the invariant documented in ``docs/SAFETY.md``.
"""

import string
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from bekas.safety import (
    _HARD_EXCLUSIONS,
    _PROTECTED_EXACT,
    _SENSITIVE_NAMES,
    is_excluded,
    is_safe_to_delete,
)

_POSIX_HARD_EXCLUSIONS = [p for p in _HARD_EXCLUSIONS if p.startswith("/")]

# Names that are sensitive *directories* (``.netrc`` is a file and is
# covered separately by the filename properties).
_SENSITIVE_DIRS = sorted(_SENSITIVE_NAMES - {".netrc"})

_COMPONENT_ALPHABET = string.ascii_letters + string.digits + "-_"


def _components():
    return st.text(_COMPONENT_ALPHABET, min_size=1, max_size=16).filter(lambda s: s not in (".", ".."))


def _suffixes():
    return st.lists(_components(), min_size=0, max_size=3)


def _abs_path():
    return st.lists(_components(), min_size=1, max_size=4).map(lambda parts: "/" + "/".join(parts))


def _is_false(safe: bool, reason: str, path: str) -> None:
    assert safe is False, f"expected {path!r} to be excluded (reason={reason!r})"


@settings(max_examples=100, deadline=None)
@given(base=st.sampled_from(_POSIX_HARD_EXCLUSIONS), suffixes=_suffixes())
def test_no_hard_system_path_is_ever_safe(base, suffixes):
    """Any path at or under a hard system exclusion is never (True, _)."""
    path = base if not suffixes else base + "".join(f"/{s}" for s in suffixes)
    safe, reason = is_safe_to_delete(path)
    _is_false(safe, reason, path)


@settings(max_examples=40, deadline=None)
@given(base=st.sampled_from(_PROTECTED_EXACT))
def test_protected_roots_are_never_safe(base):
    """Protected roots (/, /home, /tmp, ...) must never be candidates."""
    safe, reason = is_safe_to_delete(base)
    _is_false(safe, reason, base)


def test_home_and_system_roots_are_never_safe():
    """$HOME itself, /home and / are never safe as whole directories."""
    for path in (str(Path.home()), "/home", "/"):
        safe, reason = is_safe_to_delete(path)
        _is_false(safe, reason, path)


@settings(max_examples=100, deadline=None)
@given(padding=_suffixes(), name=st.sampled_from(_SENSITIVE_DIRS), deeper=_suffixes())
def test_sensitive_dirs_are_excluded(padding, name, deeper):
    """Sensitive directories (.ssh, .git, ...) and their contents are excluded."""
    target = Path.home().joinpath(*padding, name, *deeper)
    safe, reason = is_safe_to_delete(str(target))
    _is_false(safe, reason, str(target))


@settings(max_examples=50, deadline=None)
@given(padding=_suffixes())
def test_netrc_file_is_excluded(padding):
    """``.netrc`` file names are excluded wherever they appear."""
    target = Path.home().joinpath(*padding, ".netrc")
    safe, reason = is_safe_to_delete(str(target))
    _is_false(safe, reason, str(target))


@settings(max_examples=100, deadline=None)
@given(stem=_components(), ext=st.sampled_from(["key", "pem", "gpg"]))
def test_sensitive_file_suffixes_are_excluded(stem, ext):
    """``*.key`` / ``*.pem`` / ``*.gpg`` files are always excluded."""
    target = Path.home() / f"{stem}.{ext}"
    safe, reason = is_safe_to_delete(str(target))
    _is_false(safe, reason, str(target))


@settings(max_examples=100, deadline=None)
@given(parts=_suffixes().map(tuple))
def test_traversal_component_is_always_excluded(parts):
    """Any path with a '..' component is rejected regardless of position."""
    for position in range(len(parts) + 1):
        pieces = list(parts)
        pieces.insert(position, "..")
        path = "/" + "/".join(pieces)
        assert is_excluded(path), f"traversal not detected: {path!r}"


@settings(max_examples=150, deadline=None)
@given(path=_abs_path())
def test_is_excluded_agrees_with_is_safe_to_delete(path):
    """``is_excluded`` must stay consistent with ``is_safe_to_delete``."""
    safe, _ = is_safe_to_delete(path)
    assert is_excluded(path) == (not safe)


@settings(max_examples=100, deadline=None)
@given(path=_abs_path())
def test_explicit_user_exclusion_is_honoured(path):
    """A user exclusion naming the exact path always excludes it."""
    assert is_excluded(path, user_exclusions=[path])
    assert not is_safe_to_delete(path, user_exclusions=[path])[0]
