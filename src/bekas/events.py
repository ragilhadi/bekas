"""Structured event log — append-only JSONL for audit/plan/clean/undo."""

from __future__ import annotations

import glob
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bekas.config import data_dir


class Event(BaseModel):
    """A single structured event in the JSONL log.

    Attributes:
        ts: ISO-8601 timestamp.
        event: Event type (e.g. ``"clean.apply"``).
        run_id: Run identifier.
        profile: Active profile name.
        plan_signature: Optional signed plan signature.
        items_total: Total items processed.
        items_quarantined: Number quarantined.
        items_deleted: Number permanently deleted.
        bytes_reclaimed: Bytes freed.
        duration_ms: Wall-clock duration.
        errors: List of error strings.
    """

    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    event: str
    run_id: str
    profile: str = "default"
    plan_signature: str | None = None
    items_total: int = 0
    items_quarantined: int = 0
    items_deleted: int = 0
    bytes_reclaimed: int = 0
    duration_ms: int | None = None
    errors: list[str] = Field(default_factory=list)


def _events_path() -> Path:
    """Return the current events JSONL file path."""
    return data_dir() / "events.jsonl"


def _rotate_if_needed(path: Path, max_bytes: int = 10 * 1024 * 1024, keep: int = 5) -> None:
    """Rotate the event log if it exceeds ``max_bytes``.

    Args:
        path: Path to the active events file.
        max_bytes: Rotation threshold. Defaults to 10 MB.
        keep: Number of rotated files to retain. Defaults to 5.
    """
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    # Rotate: events.jsonl -> events.1.jsonl, events.1.jsonl -> events.2.jsonl, ...
    for i in range(keep - 1, 0, -1):
        old = path.with_suffix(f".{i}.jsonl")
        new = path.with_suffix(f".{i + 1}.jsonl")
        if old.exists():
            shutil.move(str(old), str(new))
    shutil.move(str(path), str(path.with_suffix(".1.jsonl")))
    # Clean up oldest if it exists beyond keep
    oldest = path.with_suffix(f".{keep + 1}.jsonl")
    if oldest.exists():
        oldest.unlink()


def log_event(event: Event) -> None:
    """Append an event to the JSONL log.

    Args:
        event: Event to persist.
    """
    path = _events_path()
    _rotate_if_needed(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(event.model_dump_json() + "\n")


def read_events(since_hours: int | None = None) -> list[dict[str, Any]]:
    """Read events from the JSONL log, optionally filtering by age.

    Args:
        since_hours: If given, only return events newer than this many hours.

    Returns:
        List of event dictionaries (newest first).
    """
    path = _events_path()
    files = [path]
    # Include rotated files
    rotated = sorted(glob.glob(str(path.with_suffix(".*.jsonl"))))
    files.extend(rotated)

    events: list[dict[str, Any]] = []
    cutoff: datetime | None = None
    if since_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=since_hours)

    for fp in files:
        p = Path(fp)
        if not p.exists():
            continue
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if cutoff is not None:
                        try:
                            ev_ts = datetime.fromisoformat(ev.get("ts", "").replace("Z", "+00:00"))
                            if ev_ts < cutoff:
                                continue
                        except Exception:
                            continue
                    events.append(ev)
        except OSError:
            continue

    # Newest first
    events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return events
