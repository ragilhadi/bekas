"""Clean runner — applies a plan, optionally with dry-run."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from bekas.config import profile_for, quarantine_dir
from bekas.database import log_run
from bekas.models import AuditReport, Candidate, Context, Plan, RemovalResult, RunResult
from bekas.plugin import Plugin
from bekas.quarantine import move_to_quarantine, purge_old_quarantine
from bekas.safety import is_excluded


def _find_plugin(plugins: list[Plugin], name: str) -> Plugin | None:
    for p in plugins:
        if p.name == name:
            return p
    return None


def _group_by_category(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        cat = c.category.split(".")[0]
        groups.setdefault(cat, []).append(c)
    return groups


def apply_plan(
    plan: Plan,
    plugins: list[Plugin],
    ctx: Context,
    audit: AuditReport | None = None,
    yes_all: bool = False,
    quarantine_enabled: bool = False,
    profile_name: str | None = None,
) -> RunResult:
    _ = audit  # reserved for future use
    profile = profile_for(profile_name)
    quarantine_enabled = quarantine_enabled or profile.get("quarantine_enabled", False)
    retention = profile.get("quarantine_retention_days", 30)

    # Purge old quarantine first
    purge_old_quarantine(retention)

    groups = _group_by_category(plan.candidates)
    per_candidate: list[tuple[Candidate, RemovalResult]] = []
    total_freed = 0
    run_id = f"run_{uuid.uuid4().hex[:8]}"

    for cat, items in groups.items():
        size = sum(c.size_bytes for c in items)
        if not yes_all and not ctx.dry_run:
            # Interactive prompt per category
            resp = input(f"About to delete {len(items)} {cat} items ({_human_size(size)}). Confirm? [y/N] ")
            if resp.strip().lower() != "y":
                continue

        for c in items:
            if is_excluded(c.path_or_handle):
                continue
            plugin = _find_plugin(plugins, c.category)
            removal_result: RemovalResult
            if plugin is None:
                # Fall back to generic deletion if no plugin matched category exactly
                removal_result = _generic_remove(c, ctx, quarantine_enabled, run_id)
            else:
                if ctx.dry_run:
                    removal_result = RemovalResult(success=True, bytes_freed=0, log="dry-run")
                else:
                    if quarantine_enabled and plugin.supports_quarantine():
                        removal_result = plugin.quarantine(c, ctx, str(quarantine_dir()))
                        # Quarantine counts as reclamation regardless of undo_token presence
                        if removal_result.success:
                            total_freed += removal_result.bytes_freed
                    else:
                        removal_result = plugin.remove(c, ctx)
                        if removal_result.success:
                            total_freed += removal_result.bytes_freed
            per_candidate.append((c, removal_result))

    run_result = RunResult(
        run_id=run_id,
        timestamp=datetime.now(UTC),
        audit_id=plan.audit_id,
        per_candidate=per_candidate,
        total_bytes_freed=total_freed,
    )
    if not ctx.dry_run:
        log_run(run_result)
    return run_result


def _generic_remove(candidate: Candidate, ctx: Context, quarantine_enabled: bool, run_id: str) -> RemovalResult:
    p = Path(candidate.path_or_handle)
    if not p.exists():
        return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")

    if ctx.dry_run:
        return RemovalResult(success=True, bytes_freed=0, log="dry-run")

    if quarantine_enabled:
        try:
            dest = move_to_quarantine(
                run_id, p, candidate.category, candidate.size_bytes, candidate.metadata
            )
            return RemovalResult(
                success=True, bytes_freed=candidate.size_bytes, undo_token=str(dest),
                log="quarantined",
            )
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    import shutil
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return RemovalResult(success=True, bytes_freed=candidate.size_bytes, log="deleted")
    except Exception as exc:
        return RemovalResult(success=False, bytes_freed=0, log=str(exc))


def _human_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"
