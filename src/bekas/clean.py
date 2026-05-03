"""Clean runner — applies a plan, optionally with dry-run."""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bekas.config import profile_for, quarantine_dir
from bekas.database import log_run
from bekas.models import AuditReport, Candidate, Confidence, Context, Plan, RemovalResult, RunResult
from bekas.plugin import Plugin
from bekas.quarantine import move_to_quarantine, purge_old_quarantine
from bekas.safety import is_excluded


def _find_plugin(plugins: list[Plugin], name: str) -> Plugin | None:
    """Find a plugin by its exact name.

    Args:
        plugins: List of loaded plugins.
        name: Plugin name to match against ``Plugin.name``.

    Returns:
        The matching plugin, or None if not found.
    """
    for p in plugins:
        if p.name == name:
            return p
    return None


def _group_by_category(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    """Group candidates by their top-level category prefix.

    Args:
        candidates: Candidates to group.

    Returns:
        Mapping from category prefix (before the first '.') to candidates.
    """
    groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        cat = c.category.split(".")[0]
        groups.setdefault(cat, []).append(c)
    return groups


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


def validate_plan_freshness(plan: Plan, force_stale: bool = False) -> tuple[list[Candidate], list[str]]:
    """Re-stat each candidate in a plan and abort or warn on drift.

    For each candidate, recompute the cheap fingerprint ``(exists, size_bytes, mtime)``
    and compare it to the stored fingerprint (if available). Candidates whose paths
    no longer exist are skipped silently. Candidates that grew or have a newer mtime
    are skipped unless ``force_stale`` is True.

    Args:
        plan: Plan to validate.
        force_stale: If True, proceed even with stale candidates.

    Returns:
        Tuple of ``(valid_candidates, skip_reasons)``. ``valid_candidates`` are safe to
        apply. ``skip_reasons`` are human-readable strings for the audit log.
    """
    valid: list[Candidate] = []
    skipped: list[str] = []
    now = datetime.now(UTC)

    # Warn if plan is older than 7 days
    if plan.signed_at is not None and (now - plan.signed_at) > timedelta(days=7):
        skipped.append(f"Plan signed on {plan.signed_at.isoformat()} (>7 days old)")

    for c in plan.candidates:
        path = Path(c.path_or_handle)
        fp = plan.fingerprints.get(c.id, {})

        # Path no longer exists → skip silently
        if not path.exists():
            continue

        try:
            stat = path.stat()
            current_size = stat.st_size if path.is_file() else c.size_bytes
            current_mtime = int(stat.st_mtime)
        except (OSError, ValueError):
            continue

        old_size = fp.get("size_bytes")
        old_mtime = fp.get("mtime")

        # No fingerprint stored → assume valid but warn
        if old_size is None and old_mtime is None:
            valid.append(c)
            continue

        grew = old_size is not None and current_size > old_size
        newer = old_mtime is not None and current_mtime > old_mtime

        if grew or newer:
            reason_parts: list[str] = []
            if grew:
                reason_parts.append(f"size changed {_human_size(old_size or 0)} → {_human_size(current_size)}")
            if newer:
                reason_parts.append("mtime newer")
            reason = f"stale: {c.id} — {', '.join(reason_parts)}"
            if force_stale:
                valid.append(c)
                skipped.append(f"{reason} (forced)")
            else:
                skipped.append(reason)
            continue

        # Shrunk or unchanged → proceed
        valid.append(c)

    return valid, skipped


def typed_confirmation_gate(
    plan: Plan,
    quarantine_enabled: bool,
    yes_all: bool = False,
    non_interactive: bool = False,
) -> bool:
    """Show a summary and require typed user confirmation before destructive action.

    For normal plans, requires typing ``"yes"``. For high-risk plans (anything
    ``MANUAL``-tier or hard-delete on > 5 GB), requires a random 4-character token.

    Args:
        plan: Plan to confirm.
        quarantine_enabled: Whether quarantine is active.
        yes_all: If True, skip the gate entirely.
        non_interactive: If True and ``yes_all`` is False, exit without changes.

    Returns:
        True if the user confirmed, False otherwise.
    """
    if yes_all:
        return True

    if non_interactive:
        return False

    groups = _group_by_category(plan.candidates)
    total_items = len(plan.candidates)
    total_size = plan.total_bytes()

    lines: list[str] = []
    lines.append(f"\nAbout to remove {total_items} items, {_human_size(total_size)} total:")
    for cat, items in groups.items():
        size = sum(c.size_bytes for c in items)
        action = "quarantine" if quarantine_enabled else "permanent"
        lines.append(f"  {cat:30s} {len(items):3d} items · {_human_size(size):>8s}  → {action}")

    qdir = quarantine_dir()
    lines.append(f"\nQuarantine: {'enabled' if quarantine_enabled else 'disabled'} ({qdir})")
    from bekas.database import runs_db_path

    lines.append(f"Undo log:   {runs_db_path()}")

    # Determine if high-risk
    has_manual = any(c.confidence == Confidence.MANUAL for c in plan.candidates)
    hard_delete_gb = sum(c.size_bytes for c in plan.candidates if c.confidence != Confidence.SAFE) / (1024**3)
    high_risk = has_manual or (not quarantine_enabled and hard_delete_gb > 5)

    if high_risk:
        token = secrets.token_urlsafe(4)[:4].upper()
        lines.append(f"\n⚠  HIGH RISK detected. Type the token '{token}' to proceed, or Ctrl-C to abort:")
        print("\n".join(lines))
        user_input = input("Token: ").strip().upper()
        return user_input == token

    lines.append('\nType "yes" to proceed, or Ctrl-C to abort:')
    print("\n".join(lines))
    user_input = input().strip().lower()
    return user_input == "yes"


def apply_plan(
    plan: Plan,
    plugins: list[Plugin],
    ctx: Context,
    audit: AuditReport | None = None,
    yes_all: bool = False,
    quarantine_enabled: bool = False,
    profile_name: str | None = None,
    non_interactive: bool = False,
    force_stale: bool = False,
) -> RunResult:
    """Apply a cleanup plan and return the run result.

    Iterates over plan candidates grouped by category, optionally
    prompting for confirmation, then delegates removal to the
    matching plugin or falls back to generic deletion.

    Args:
        plan: The plan to apply.
        plugins: Available plugins for category-specific removal.
        ctx: Execution context (controls dry-run and verbosity).
        audit: Optional originating audit report (reserved for future use).
        yes_all: If True, skip interactive per-category confirmation.
        quarantine_enabled: If True, quarantine instead of deleting when supported.
        profile_name: Optional profile name to load settings from.
        non_interactive: If True, requires ``yes_all`` or exits.
        force_stale: If True, apply even if plan candidates have drifted.

    Returns:
        RunResult summarizing the operation.
    """
    _ = audit  # reserved for future use
    profile = profile_for(profile_name)
    quarantine_enabled = quarantine_enabled or profile.get("quarantine_enabled", False)
    retention = profile.get("quarantine_retention_days", 30)

    # Re-validate plan freshness
    if plan.fingerprints:
        valid, skipped = validate_plan_freshness(plan, force_stale=force_stale)
        if skipped and ctx.verbose:
            for reason in skipped:
                print(f"  [plan validation] {reason}")
        plan.candidates = valid

    # Purge old quarantine first
    purge_old_quarantine(retention)

    if not plan.candidates:
        return RunResult(
            run_id=f"run_{uuid.uuid4().hex[:8]}",
            audit_id=plan.audit_id,
            per_candidate=[],
            total_bytes_freed=0,
        )

    # Typed confirmation gate
    if not ctx.dry_run:
        if non_interactive and not yes_all:
            print("Error: --non-interactive requires --yes-all.")
            os._exit(2)  # noqa: S607
        confirmed = typed_confirmation_gate(plan, quarantine_enabled, yes_all=yes_all, non_interactive=non_interactive)
        if not confirmed:
            print("Aborted. No changes were made.")
            os._exit(1)  # noqa: S607

    groups = _group_by_category(plan.candidates)
    per_candidate: list[tuple[Candidate, RemovalResult]] = []
    total_freed = 0
    run_id = f"run_{uuid.uuid4().hex[:8]}"

    for cat, items in groups.items():
        size = sum(c.size_bytes for c in items)
        if not yes_all and not ctx.dry_run:
            # Interactive prompt per category (secondary confirmation)
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
                if removal_result.success:
                    total_freed += removal_result.bytes_freed
            else:
                if ctx.dry_run:
                    removal_result = RemovalResult(success=True, bytes_freed=0, log="dry-run")
                else:
                    if quarantine_enabled and plugin.supports_quarantine():
                        removal_result = plugin.quarantine(c, ctx, str(quarantine_dir()), run_id=run_id)
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
    """Perform generic removal for a candidate without a matching plugin.

    Args:
        candidate: Candidate to remove.
        ctx: Execution context (dry_run is respected).
        quarantine_enabled: If True, move to quarantine instead of deleting.
        run_id: Run identifier for quarantine tracking.

    Returns:
        Result of the removal or quarantine operation.
    """
    p = Path(candidate.path_or_handle)
    if not p.exists():
        return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")

    if ctx.dry_run:
        return RemovalResult(success=True, bytes_freed=0, log="dry-run")

    if quarantine_enabled:
        try:
            dest = move_to_quarantine(run_id, p, candidate.category, candidate.size_bytes, candidate.metadata)
            return RemovalResult(
                success=True,
                bytes_freed=candidate.size_bytes,
                undo_token=str(dest),
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
