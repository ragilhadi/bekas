"""Size, age and multi-format output helpers shared by audit/plan/clean (issue #21)."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable

from bekas.models import AuditReport, Candidate, Plan, RunResult

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?b?|b)\s*$", re.IGNORECASE)
_AGE_RE = re.compile(r"^\s*(\d+)\s*([smhdwy])a?s?\s*$", re.IGNORECASE)

_UNIT_MULTIPLIERS: dict[str, int] = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}

_AGE_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
    "y": 31_557_600,
}


def parse_size(text: str) -> int:
    """Parse a human-readable byte size such as ``500MB``.

    Args:
        text: Size with optional unit (plain integers are bytes).

    Returns:
        Size in bytes.

    Raises:
        ValueError: When the text is not a valid size expression.
    """
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"Invalid size: {text!r} (e.g. 500MB, 1.5GB, 2048)")
    value = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    return int(value * _UNIT_MULTIPLIERS[unit])


def parse_age(text: str) -> int:
    """Parse a human-readable duration such as ``90d`` into seconds.

    Args:
        text: Duration with unit: s, m(in), h, d, w(eek), y(ear).

    Returns:
        Duration in seconds.

    Raises:
        ValueError: When the text is not a valid age expression.
    """
    match = _AGE_RE.match(text)
    if not match:
        raise ValueError(f"Invalid age: {text!r} (e.g. 90d, 2w, 1y)")
    return int(match.group(1)) * _AGE_UNIT_SECONDS[match.group(2).lower()]


def apply_filters(
    candidates: list[Candidate],
    min_size: int | None = None,
    older_than: int | None = None,
    now_ts: float | None = None,
) -> list[Candidate]:
    """Filter candidates by minimum size and/or minimum age.

    Args:
        candidates: Candidates to filter.
        min_size: Minimum ``size_bytes`` in bytes (inclusive).
        older_than: Minimum age in seconds, based on ``mtime``; candidates
            without an ``mtime`` never pass an age filter (safer default).
        now_ts: Reference time for age computation (defaults to ``time.time()``).

    Returns:
        Candidates passing every supplied filter, in input order.
    """
    import time

    ref = now_ts if now_ts is not None else time.time()
    out: list[Candidate] = []
    for c in candidates:
        too_small = min_size is not None and c.size_bytes < min_size
        too_young = older_than is not None and (c.mtime is None or (ref - c.mtime) < older_than)
        if too_small or too_young:
            continue
        out.append(c)
    return out


# --- Output formats -------------------------------------------------------


def _rows_for_candidates(candidates: list[Candidate]) -> list[dict[str, object]]:
    return [
        {
            "id": c.id,
            "category": c.category,
            "size_bytes": c.size_bytes,
            "path_or_handle": c.path_or_handle,
            "confidence": str(c.confidence.value),
            "reason": c.reason,
        }
        for c in candidates
    ]


def _to_csv(rows: Iterable[dict[str, object]]) -> str:
    buf = io.StringIO()
    rows = list(rows)
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["id"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def format_output(
    obj: AuditReport | Plan | RunResult | list[Candidate],
    fmt: str,
    json_fallback: str,
) -> str:
    """Render an object in the requested output format.

    Args:
        obj: Any of the supported payload objects (or a candidate list).
        fmt: One of ``table`` (falls back to ``json_fallback``), ``json``,
            ``ndjson``, ``csv``.
        json_fallback: Pre-rendered human/table text used for ``table``.

    Returns:
        The formatted output string (no trailing newline management).
    """
    if fmt == "table":
        return json_fallback

    if isinstance(obj, list):
        rows = _rows_for_candidates(obj)
    elif isinstance(obj, AuditReport):
        rows = [row for pr in obj.plugins for row in _rows_for_candidates(pr.candidates)]
    elif isinstance(obj, Plan):
        rows = _rows_for_candidates(obj.candidates)
    elif isinstance(obj, RunResult):
        rows = [
            {
                "run_id": obj.run_id,
                "candidate_id": c.id,
                "success": r.success,
                "bytes_freed": r.bytes_freed,
                "log": r.log,
            }
            for c, r in obj.per_candidate
        ]
    else:
        rows = [obj.model_dump()]  # pragma: no cover - defensive

    if fmt == "json":
        payload = obj.model_dump(mode="json") if not isinstance(obj, list) else {"candidates": obj2list(obj)}
        return json.dumps(payload, indent=2)
    if fmt == "ndjson":
        return "\n".join(json.dumps(r, default=str) for r in rows)
    if fmt == "csv":
        return _to_csv(rows)
    raise ValueError(f"Unknown format: {fmt!r} (expected table|json|ndjson|csv)")


def obj2list(obj: list[Candidate]) -> list[dict[str, object]]:
    """Serialize candidates to plain dicts."""
    return [c.model_dump(mode="json") for c in obj]


def count_by_tier(candidates: list[Candidate]) -> dict[str, int]:
    """Count candidates per confidence tier (for summaries)."""
    out: dict[str, int] = {}
    for c in candidates:
        key = str(c.confidence.value)
        out[key] = out.get(key, 0) + 1
    return out
