"""Output formatters for audit and plan results."""

from __future__ import annotations

import json

from bekas.models import AuditReport, Candidate, Plan, RunResult


def format_human(report: AuditReport | Plan | RunResult) -> str:
    """Format a report, plan, or run result as human-readable text.

    Args:
        report: Object to format.

    Returns:
        Multi-line human-readable string.
    """
    lines: list[str] = []
    if isinstance(report, AuditReport):
        lines.append(f"Audit complete in {report.duration_ms // 1000}s.")
        lines.append("")
        current_prefix = ""
        for pr in report.plugins:
            prefix = pr.name.split(".")[0].capitalize()
            if prefix != current_prefix:
                current_prefix = prefix
                lines.append(f"{prefix}:")
            safe = sum(1 for c in pr.candidates if c.confidence.value == "safe")
            size = sum(c.size_bytes for c in pr.candidates)
            lines.append(f"  {pr.name:30s} {pr.candidates_found:3d} found, {safe:3d} safe    {_human_size(size)}")
        lines.append("")
        summary = report.summary
        lines.append(f"Total reclaimable:    {_human_size(summary.total_bytes)}")
        for conf, vals in summary.by_confidence.items():
            lines.append(f"  {conf.capitalize():18s} {vals['count']:3d} items   {_human_size(vals['bytes'])}")
        return "\n".join(lines)

    if isinstance(report, Plan):
        lines.append("Plan preview:")
        for cat, items in report.by_category().items():
            size = sum(c.size_bytes for c in items)
            lines.append(f"  {cat:30s} {len(items):3d} items   {_human_size(size)}")
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
    for conf, vals in summary.by_confidence.items():
        lines.append(f"- **{conf.capitalize()}**: {vals['count']} items ({_human_size(vals['bytes'])})")
    return "\n".join(lines)


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
