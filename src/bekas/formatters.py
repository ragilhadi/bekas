"""Output formatters for audit and plan results."""

from __future__ import annotations

import json

from bekas.models import AuditReport, Candidate, Confidence, Plan, RunResult


def _totals_header(report: AuditReport) -> list[str]:
    """Build the headline totals block for an audit report.

    Args:
        report: Audit report to build the header from.

    Returns:
        List of formatted lines.
    """
    summary = report.summary
    total = summary.total_candidates
    total_bytes = summary.total_bytes
    lines: list[str] = []
    lines.append("╭─ bekas audit ─────────────────────────────────────────╮")
    lines.append(f"│ {total} candidates · {_human_size(total_bytes)} reclaimable                  │")
    tiers: list[str] = []
    for conf in Confidence:
        val = summary.by_confidence.get(conf.value, {})
        if val.get("count", 0):
            tiers.append(f"  {conf.value.upper():6s} {_human_size(val.get('bytes', 0))}")
    if tiers:
        tier_line = "  ·  ".join(tiers)
        lines.append(f"│{tier_line:55s}│")
    lines.append("╰───────────────────────────────────────────────────────╯")
    return lines


def format_human(
    report: AuditReport | Plan | RunResult,
    sort_by: str | None = None,
    top: int | None = None,
) -> str:
    """Format a report, plan, or run result as human-readable text.

    Args:
        report: Object to format.
        sort_by: Optional sort key for candidates inside reports and plans.
            One of ``"size"``, ``"age"``, ``"tier"``. Defaults to None (original order).
        top: Optional limit on the number of candidates shown per category.
            Defaults to None (show all).

    Returns:
        Multi-line human-readable string.
    """
    lines: list[str] = []
    if isinstance(report, AuditReport):
        lines.extend(_totals_header(report))
        lines.append("")
        lines.append(f"Audit complete in {report.duration_ms // 1000}s.")
        lines.append("")
        current_prefix = ""
        for pr in report.plugins:
            prefix = pr.name.split(".")[0].capitalize()
            if prefix != current_prefix:
                current_prefix = prefix
                lines.append(f"{prefix}:")
            candidates = _sort_and_limit(pr.candidates, sort_by, top)
            safe = sum(1 for c in pr.candidates if c.confidence.value == "safe")
            size = sum(c.size_bytes for c in pr.candidates)
            lines.append(f"  {pr.name:30s} {pr.candidates_found:3d} found, {safe:3d} safe    {_human_size(size)}")
            for c in candidates:
                flag = "~" if c.metadata.get("size_approximate") else " "
                lines.append(f"    [{c.confidence.value:6s}] {flag}{c.id} — {_human_size(c.size_bytes)}")
        lines.append("")
        summary = report.summary
        lines.append(f"Total reclaimable:    {_human_size(summary.total_bytes)}")
        for conf in Confidence:
            vals = summary.by_confidence.get(conf.value, {})
            if vals.get("count", 0):
                lines.append(f"  {conf.value.capitalize():18s} {vals['count']:3d} items   {_human_size(vals['bytes'])}")
        return "\n".join(lines)

    if isinstance(report, Plan):
        lines.append("Plan preview:")
        candidates = _sort_and_limit(report.candidates, sort_by, top)
        groups: dict[str, list[Candidate]] = {}
        for c in candidates:
            groups.setdefault(c.category, []).append(c)
        for cat, items in groups.items():
            size = sum(c.size_bytes for c in items)
            lines.append(f"  {cat:30s} {len(items):3d} items   {_human_size(size)}")
            for c in items:
                flag = "~" if c.metadata.get("size_approximate") else " "
                lines.append(f"    {flag}{c.id} [{c.confidence.value}] {_human_size(c.size_bytes)}")
        lines.append(f"\nTotal: {_human_size(report.total_bytes())}")
        return "\n".join(lines)

    if isinstance(report, RunResult):
        lines.append(f"Run {report.run_id} completed.")
        lines.append(f"Total freed: {_human_size(report.total_bytes_freed)}")
        for c, r in report.per_candidate:
            status = "OK" if r.success else "FAIL"
            lines.append(f"  [{status}] {c.id} — {_human_size(r.bytes_freed)}")
        return "\n".join(lines)

    return ""


def format_json(report: AuditReport | Plan | RunResult) -> str:
    """Format a report, plan, or run result as indented JSON.

    Args:
        report: Object to serialize.

    Returns:
        Indented JSON string.
    """
    if isinstance(report, AuditReport):
        return json.dumps(report.model_dump(mode="json"), indent=2)
    if isinstance(report, Plan):
        return json.dumps(report.model_dump(mode="json"), indent=2)
    if isinstance(report, RunResult):
        return json.dumps(report.model_dump(mode="json"), indent=2)
    return "{}"


def format_md(report: AuditReport) -> str:
    """Format an AuditReport as Markdown.

    Args:
        report: Audit report to format.

    Returns:
        Markdown string.
    """
    lines: list[str] = []
    lines.append("# Bekas Audit Report")
    lines.append(f"\n- **Audit ID**: {report.audit_id}")
    lines.append(f"- **Duration**: {report.duration_ms // 1000}s")
    lines.append(f"- **OS**: {report.system.os} ({report.system.arch})")
    lines.append(f"- **Free disk**: {_human_size(report.system.free_disk_bytes)}")
    lines.append("")
    lines.append("| Plugin | Found | Safe | Size |")
    lines.append("|--------|------:|-----:|-----:|")
    for pr in report.plugins:
        safe = sum(1 for c in pr.candidates if c.confidence.value == "safe")
        size = sum(c.size_bytes for c in pr.candidates)
        lines.append(f"| {pr.name} | {pr.candidates_found} | {safe} | {_human_size(size)} |")
    lines.append("")
    summary = report.summary
    lines.append(f"**Total reclaimable**: {_human_size(summary.total_bytes)}")
    for conf in Confidence:
        vals = summary.by_confidence.get(conf.value, {})
        if vals.get("count", 0):
            lines.append(f"- **{conf.capitalize()}**: {vals['count']} items ({_human_size(vals['bytes'])})")
    return "\n".join(lines)


def _sort_and_limit(
    candidates: list[Candidate],
    sort_by: str | None = None,
    top: int | None = None,
) -> list[Candidate]:
    """Sort candidates by the given key and optionally limit to top N.

    Args:
        candidates: Candidates to sort and filter.
        sort_by: Sort key — ``"size"``, ``"age"``, or ``"tier"``. None keeps original order.
        top: Maximum number of candidates to return. None returns all.

    Returns:
        Sorted and optionally truncated candidate list.
    """
    result = list(candidates)
    if sort_by == "size":
        result.sort(key=lambda c: c.size_bytes, reverse=True)
    elif sort_by == "age":
        # Candidates without mtime sort last (infinite age)
        result.sort(key=lambda c: c.mtime if c.mtime is not None else -1)
    elif sort_by == "tier":
        order = {Confidence.SAFE: 0, Confidence.REVIEW: 1, Confidence.MANUAL: 2}
        result.sort(key=lambda c: order.get(c.confidence, 99))
    if top is not None and top > 0:
        result = result[:top]
    return result


def _human_size(size_bytes: int) -> str:
    """Convert a byte size into a human-readable string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Human-readable size (e.g., "1.5 MB").
    """
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_candidates(candidates: list[Candidate]) -> str:
    """Format a list of candidates as human-readable text.

    Args:
        candidates: Candidates to format.

    Returns:
        Multi-line string with IDs, confidence tiers, sizes, reasons, and paths.
    """
    lines: list[str] = []
    for c in candidates:
        lines.append(f"{c.id} [{c.confidence.value}] {_human_size(c.size_bytes)}")
        lines.append(f"  Reason: {c.reason}")
        lines.append(f"  Path: {c.path_or_handle}")
    return "\n".join(lines)
