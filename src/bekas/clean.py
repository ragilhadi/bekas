"""Clean runner — applies a plan, optionally with dry-run."""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bekas.config import load_config, profile_for, quarantine_dir
from bekas.database import log_run
from bekas.events import Event, log_event
from bekas.models import AuditReport, Candidate, Confidence, Context, Plan, RemovalResult, RunResult
from bekas.plugin import Plugin
from bekas.quarantine import move_to_quarantine, purge_old_quarantine
from bekas.safety import is_safe_to_delete


class CleanAborted(Exception):  # noqa: N818
    """Raised when a clean run is stopped before any change is made.

    Attributes:
        exit_code: Process exit status the CLI should use.
    """

    def __init__(self, message: str, exit_code: int = 1) -> None:
        """Initialize with a message and the CLI exit status.

        Args:
            message: Human-readable reason shown to the user.
            exit_code: Exit status for the process.
        """
        super().__init__(message)
        self.exit_code = exit_code


def _find_plugin(plugins: list[Plugin], name: str) -> Plugin | None:
    """Find the plugin that owns a candidate category.

    A plugin owns a category when it equals ``Plugin.name`` or is listed in
    ``Plugin.categories`` (plugins such as ``docker.images`` emit more specific
    categories like ``docker.image.dangling``).

    Args:
        plugins: List of loaded plugins.
        name: Candidate category to resolve.

    Returns:
        The matching plugin, or None if not found.
    """
    for p in plugins:
        if p.name == name or name in p.categories:
            return p
    return None


def _is_filesystem_path(handle: str) -> bool:
    """Return True if a candidate handle is a plain absolute filesystem path.

    Opaque handles such as ``docker://<id>`` are never filesystem paths.

    Args:
        handle: Candidate ``path_or_handle`` value.

    Returns:
        True when the handle can safely be treated as a path.
    """
    return "://" not in handle and Path(handle).is_absolute()


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


# Candidates whose ``path_or_handle`` is a container for the real target (a git
# branch lives in a repository, which changes with every commit), so the path
# says nothing about whether the candidate itself changed.
_NO_FINGERPRINT_CATEGORIES = frozenset({"git.branch"})

# Upper bound on directory entries visited when measuring a candidate. Keeps
# ``plan --save`` and ``clean`` fast on enormous trees; the result stays
# deterministic for an unchanged tree.
_MAX_FINGERPRINT_ENTRIES = 50_000


def _measure_tree(root: Path) -> tuple[int, int]:
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
                if visited > _MAX_FINGERPRINT_ENTRIES:
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


def _measure_path(path: Path) -> tuple[int, int]:
    """Return ``(size_bytes, mtime)`` for a file or directory.

    Args:
        path: Existing file or directory.

    Returns:
        Current size and newest modification time (epoch seconds).

    Raises:
        OSError: If the path cannot be inspected.
    """
    if path.is_dir():
        return _measure_tree(path)
    st = path.stat()
    return st.st_size, int(st.st_mtime)


def fingerprint_candidate(candidate: Candidate) -> dict[str, object]:
    """Capture a freshness fingerprint for a path-based candidate.

    Args:
        candidate: Candidate to fingerprint.

    Returns:
        ``{"exists", "size_bytes", "mtime"}``, or an empty dict when the candidate
        has no meaningful path (opaque handles, git branches, missing paths).
    """
    if not _is_filesystem_path(candidate.path_or_handle) or candidate.category in _NO_FINGERPRINT_CATEGORIES:
        return {}
    path = Path(candidate.path_or_handle)
    try:
        if not path.exists():
            return {}
        size, mtime = _measure_path(path)
    except OSError:
        return {}
    return {"exists": True, "size_bytes": size, "mtime": mtime}


def fingerprint_plan(plan: Plan) -> dict[str, dict[str, object]]:
    """Fingerprint every candidate of a plan that can be fingerprinted.

    Args:
        plan: Plan whose candidates should be fingerprinted.

    Returns:
        Mapping from candidate id to fingerprint (candidates without one are omitted).
    """
    out: dict[str, dict[str, object]] = {}
    for c in plan.candidates:
        fp = fingerprint_candidate(c)
        if fp:
            out[c.id] = fp
    return out


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
        # Opaque handles (docker://...) have no path to re-stat; the owning
        # plugin validates them when it removes them.
        if not _is_filesystem_path(c.path_or_handle):
            valid.append(c)
            continue

        path = Path(c.path_or_handle)
        fp = plan.fingerprints.get(c.id, {})

        # Path no longer exists → skip silently
        if not path.exists():
            continue

        try:
            current_size, current_mtime = _measure_path(path)
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
        True if the user confirmed, False otherwise (including closed stdin).
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
        try:
            user_input = input("Token: ").strip().upper()
        except EOFError:
            return False
        return user_input == token

    lines.append('\nType "yes" to proceed, or Ctrl-C to abort:')
    print("\n".join(lines))
    try:
        user_input = input().strip().lower()
    except EOFError:
        return False
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
    profile_dict = profile.model_dump() if hasattr(profile, "model_dump") else profile
    quarantine_enabled = quarantine_enabled or profile_dict.get("quarantine_enabled", False)
    retention = profile_dict.get("quarantine_retention_days", 30)

    # Re-validate plan freshness
    if plan.fingerprints or plan.signed_at is not None:
        valid, skipped = validate_plan_freshness(plan, force_stale=force_stale)
        for reason in skipped:
            print(f"  [plan validation] {reason}")
        plan.candidates = valid

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
            raise CleanAborted("Error: --non-interactive requires --yes-all.", exit_code=2)
        confirmed = typed_confirmation_gate(plan, quarantine_enabled, yes_all=yes_all, non_interactive=non_interactive)
        if not confirmed:
            raise CleanAborted("Aborted. No changes were made.", exit_code=1)

        # Only now that the user has confirmed is it acceptable to discard
        # expired quarantine entries. A failure here must not abort the clean.
        try:
            purge_old_quarantine(retention)
        except Exception as exc:
            if ctx.verbose:
                print(f"  [quarantine purge] skipped: {exc}")

    groups = _group_by_category(plan.candidates)
    per_candidate: list[tuple[Candidate, RemovalResult]] = []
    total_freed = 0
    run_id = f"run_{uuid.uuid4().hex[:8]}"

    # Re-apply the full safety policy at apply time: the config may have gained
    # exclusions since the plan was created, and files may have been touched.
    user_exclusions = load_config().exclude
    min_quiet_hours = profile.min_quiet_hours

    for items in groups.values():
        for c in items:
            safe, reason = is_safe_to_delete(c.path_or_handle, user_exclusions, min_quiet_hours)
            if not safe:
                per_candidate.append(
                    (c, RemovalResult(success=False, bytes_freed=0, log=f"skipped by safety rules: {reason}"))
                )
                continue
            plugin = _find_plugin(plugins, c.category)
            removal_result: RemovalResult
            if plugin is None:
                if not _is_filesystem_path(c.path_or_handle):
                    # Opaque handles (docker://, ...) can only be removed by the owning plugin.
                    removal_result = RemovalResult(
                        success=False,
                        bytes_freed=0,
                        log=f"No plugin handles category {c.category!r}; refusing to treat "
                        f"{c.path_or_handle!r} as a path.",
                    )
                    per_candidate.append((c, removal_result))
                    continue
                # Third-party/legacy category with a plain path: generic deletion.
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
        quarantined = sum(1 for _, r in per_candidate if r.log == "quarantined")
        deleted = sum(1 for _, r in per_candidate if r.log == "deleted")
        errors = [r.log for _, r in per_candidate if not r.success]
        log_event(
            Event(
                event="clean.apply",
                run_id=run_id,
                profile=profile_name or "default",
                items_total=len(per_candidate),
                items_quarantined=quarantined,
                items_deleted=deleted,
                bytes_reclaimed=total_freed,
                errors=errors,
            )
        )
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
