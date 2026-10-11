"""Quarantine system."""

from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bekas.config import quarantine_dir
from bekas.database import (
    add_quarantine,
    list_quarantine,
    mark_undo_restored,
    remove_quarantine_entry,
)


class PartialMoveError(OSError):
    """A cross-device move copied the data but could not remove the original.

    Attributes:
        dest: Location of the complete copy.
    """

    def __init__(self, dest: Path, cause: OSError) -> None:
        """Initialize with the complete copy and the removal error.

        Args:
            dest: Location of the complete copy.
            cause: The error raised while removing the original.
        """
        super().__init__(
            f"copied to {dest} but could not remove the original ({cause}); remaining files were left in place"
        )
        self.dest = dest


def _remove_path(path: Path) -> None:
    """Delete a file, symlink or directory tree if it exists.

    Args:
        path: Path to delete.
    """
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _safe_move(src: Path, dst: Path) -> None:
    """Move ``src`` to ``dst`` without ever leaving a half-written destination.

    Same-filesystem moves are an atomic rename. Across filesystems the data is
    copied first; if the copy fails the partial destination is removed and the
    original is untouched.

    Args:
        src: Existing file or directory.
        dst: Destination that must not exist.

    Raises:
        PartialMoveError: The copy succeeded but the original could not be fully removed.
        OSError: The move failed; the original is intact.
    """
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    try:
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
    except BaseException:
        _remove_path(dst)
        raise

    try:
        _remove_path(src)
    except OSError as exc:
        raise PartialMoveError(dst, exc) from exc


def _parse_timestamp(value: str) -> datetime | None:
    """Parse a stored ISO timestamp, treating a naive one as UTC.

    Args:
        value: Timestamp string from the database.

    Returns:
        Timezone-aware datetime, or None if it cannot be parsed.
    """
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def quarantine_path(timestamp: datetime | None = None) -> Path:
    """Generate and ensure a timestamped quarantine subdirectory.

    Args:
        timestamp: Optional timestamp to base the directory name on.
            Defaults to the current UTC time.

    Returns:
        Path to the created quarantine subdirectory.
    """
    ts = timestamp or datetime.now(UTC)
    d = quarantine_dir() / ts.strftime("%Y%m%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def move_to_quarantine(
    run_id: str,
    original: Path,
    category: str,
    size_bytes: int,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Move a file or directory into quarantine and log the operation.

    Args:
        run_id: Run identifier for audit tracking.
        original: Path to the item being quarantined.
        category: Candidate category.
        size_bytes: Size of the item in bytes.
        metadata: Optional metadata dictionary.

    Returns:
        Path to the quarantined item.

    Raises:
        OSError: If the move operation fails.
    """
    qdir = quarantine_path()
    dest = qdir / original.name
    # Avoid collision
    counter = 1
    while dest.exists():
        dest = qdir / f"{original.name}_{counter}"
        counter += 1

    try:
        _safe_move(original, dest)
    except PartialMoveError:
        # The complete copy is in quarantine, so it must be restorable even
        # though the original could not be fully removed.
        add_quarantine(run_id, category, str(original), str(dest), size_bytes, metadata)
        raise

    try:
        add_quarantine(run_id, category, str(original), str(dest), size_bytes, metadata)
    except Exception:
        # Never leave an item in quarantine that the database does not know about.
        _safe_move(dest, original)
        raise
    return dest


def restore_from_quarantine(qid: str) -> Path:
    """Restore a quarantined item to its original location.

    Args:
        qid: Quarantine identifier or quarantine path (undo_token).

    Returns:
        Path to the restored item.

    Raises:
        FileNotFoundError: If the quarantine item is not found.
        FileExistsError: If the restore destination already exists.
        OSError: If the move operation fails.
    """
    rows = list_quarantine()
    # Try lookup by quarantine_id first, then by quarantine_path (undo_token may be a path)
    row = next((r for r in rows if r["quarantine_id"] == qid), None)
    if not row:
        row = next((r for r in rows if r["quarantine_path"] == qid), None)
    if not row:
        raise FileNotFoundError(f"Quarantine item {qid} not found")
    src = Path(row["quarantine_path"])
    dest = Path(row["original_path"])
    if not src.exists() and not src.is_symlink():
        raise FileNotFoundError(f"Quarantined data is missing: {src}")
    if dest.exists():
        raise FileExistsError(f"Restore destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _safe_move(src, dest)
    remove_quarantine_entry(row["quarantine_id"])
    return dest


def purge_old_quarantine(retention_days: int = 30) -> tuple[int, int]:
    """Remove quarantined items older than the retention threshold.

    Args:
        retention_days: Age threshold in days. Items older than this are removed.
            Defaults to 30. Use 0 to purge everything.

    Returns:
        Tuple of (items_removed, bytes_freed).
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    removed = 0
    bytes_freed = 0
    for row in list_quarantine():
        ts = _parse_timestamp(row.get("timestamp", ""))
        if ts is None or ts >= cutoff:
            continue
        # One unreadable item must not stop the rest of the purge.
        try:
            p = Path(row["quarantine_path"])
            if p.exists() or p.is_symlink():
                _remove_path(p)
                removed += 1
                bytes_freed += row.get("size_bytes", 0)
            remove_quarantine_entry(row["quarantine_id"])
        except Exception:
            continue
    return removed, bytes_freed


@dataclass
class UndoOutcome:
    """Result of undoing one run.

    Attributes:
        restored: Items restored by this call.
        already_restored: Items restored by an earlier undo of the same run.
        failed: ``(undo_token, reason)`` for items that could not be restored.
        permanent: Items that were deleted without quarantine and cannot be restored.
    """

    restored: int = 0
    already_restored: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    permanent: int = 0


def undo_run_results(run_id: str, results: list[dict[str, Any]]) -> UndoOutcome:
    """Restore every quarantined item of a run, skipping ones already restored.

    Args:
        run_id: Run identifier (used to record progress).
        results: Parsed ``results_json`` of the run.

    Returns:
        Summary of what was restored, skipped and failed.
    """
    out = UndoOutcome()
    for entry in results:
        token = entry.get("undo_token")
        if not token:
            if entry.get("success") and entry.get("log") != "dry-run":
                out.permanent += 1
            continue
        if entry.get("restored_at"):
            out.already_restored += 1
            continue
        try:
            restore_from_quarantine(token)
        except Exception as exc:
            out.failed.append((token, str(exc)))
            continue
        out.restored += 1
        mark_undo_restored(run_id, token)
    return out
