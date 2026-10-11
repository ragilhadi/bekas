"""Cheap filesystem measurements shared by plan fingerprints and the audit cache."""

from __future__ import annotations

import os
from pathlib import Path

# Upper bound on directory entries visited when measuring a path. Keeps
# ``plan --save``, ``clean`` and audits fast on enormous trees; the result
# stays deterministic for an unchanged tree.
MAX_ENTRIES = 50_000


def measure_tree(root: Path) -> tuple[int, int]:
    """Measure a directory cheaply without following symlinks.

    Args:
        root: Directory to measure.

    Returns:
        ``(total_file_bytes, newest_mtime)`` over the directory and its entries.
    """
    total = 0
    newest = int(root.stat().st_mtime)
    visited = 0
    stack = [str(root)]
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for entry in it:
                visited += 1
                if visited > MAX_ENTRIES:
                    return total, newest
                try:
                    st = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                newest = max(newest, int(st.st_mtime))
                if is_dir:
                    stack.append(entry.path)
                else:
                    total += st.st_size
    return total, newest


def measure_path(path: Path) -> tuple[int, int]:
    """Return ``(size_bytes, mtime)`` for a file or directory.

    Args:
        path: Existing file or directory.

    Returns:
        Current size and newest modification time (epoch seconds).

    Raises:
        OSError: If the path cannot be inspected.
    """
    if path.is_dir():
        return measure_tree(path)
    st = path.stat()
    return st.st_size, int(st.st_mtime)
