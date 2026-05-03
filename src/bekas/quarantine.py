"""Quarantine system."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bekas.config import quarantine_dir
from bekas.database import add_quarantine, list_quarantine, remove_quarantine_entry


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

    if original.is_dir():
        shutil.move(str(original), str(dest))
    else:
        shutil.move(str(original), str(dest))

    add_quarantine(run_id, category, str(original), str(dest), size_bytes, metadata)
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
    if dest.exists():
        raise FileExistsError(f"Restore destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
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
    rows = list_quarantine()
    removed = 0
    bytes_freed = 0
    for row in rows:
        ts = datetime.fromisoformat(row["timestamp"])
        if ts < cutoff:
            p = Path(row["quarantine_path"])
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                removed += 1
                bytes_freed += row.get("size_bytes", 0)
            remove_quarantine_entry(row["quarantine_id"])
    return removed, bytes_freed
