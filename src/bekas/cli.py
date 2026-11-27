"""CLI entry point for bekas."""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from bekas.clean import CleanAborted, apply_plan, diff_plans, fingerprint_plan, select_interactive
from bekas.config import (
    config_path,
    data_dir,
    ensure_config,
    is_plugin_enabled,
    load_config,
    profile_for,
    resolved_config,
    validate_config_file,
)
from bekas.database import get_run, get_saved_audit, list_quarantine, list_runs, save_audit
from bekas.events import read_events
from bekas.formats import apply_filters, format_output, parse_age, parse_size
from bekas.formatters import format_human, format_json
from bekas.locking import AlreadyRunningError, ProcessLock
from bekas.models import AuditReport, Candidate, Confidence, Context, Plan
from bekas.plugin import Plugin, discover_plugins
from bekas.quarantine import (
    UndoOutcome,
    purge_old_quarantine,
    restore_from_quarantine,
    undo_item_ids,
    undo_run_item,
    undo_run_results,
)
from bekas.runner import run_audit
from bekas.signing import sign_plan, verify_plan


@contextlib.contextmanager
def _exclusive() -> Iterator[None]:
    """Hold the single-instance lock around a mutating command.

    Exits with status 3 (like ``clean``) when another bekas process holds it.

    Yields:
        Nothing; the lock is released when the block exits.
    """
    try:
        with ProcessLock():
            yield
    except AlreadyRunningError as exc:
        click.echo(str(exc))
        sys.exit(3)


def _get_plugins() -> list[Plugin]:
    """Discover and return all installed plugins.

    Returns:
        List of plugin instances.
    """
    return discover_plugins()


def _parse_filter_arg(raw: str, parser: Callable[[str], int]) -> int:
    """Parse a user filter argument, turning bad input into a usage error.

    Args:
        raw: Raw CLI string (e.g. ``500MB`` or ``90d``).
        parser: :func:`bekas.formats.parse_size` or :func:`bekas.formats.parse_age`.

    Returns:
        Parsed integer (bytes or seconds).
    """
    try:
        return parser(raw)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


def _send_desktop_notification(message: str) -> None:
    """Best-effort desktop notification; silently skips where unsupported.

    Args:
        message: One-line summary to display.
    """
    import shutil
    import subprocess

    if sys.platform == "darwin" and shutil.which("osascript"):
        with contextlib.suppress(Exception):
            subprocess.run(
                ["osascript", "-e", f'display notification "{message}" with title "bekas"'], timeout=3, check=False
            )
    elif shutil.which("notify-send"):
        with contextlib.suppress(Exception):
            subprocess.run(["notify-send", "bekas", message], timeout=3, check=False)


def _load_audit_ref(ref: str | None) -> AuditReport:
    """Resolve a ``--from-audit`` reference to a stored report.

    Args:
        ref: Audit id, alias, or ``last``. None means run nothing (caller skips).

    Returns:
        The stored report.

    Raises:
        SystemExit: When the reference matches no stored audit.
    """
    report = get_saved_audit(ref or "last")
    if report is None:
        click.echo(f"Error: no saved audit matching {ref or 'last'!r}. Run `bekas audit --save` first.")
        sys.exit(1)
    click.echo(f"Using saved audit {report.audit_id} (no rescan).")
    return report


def _check_tier_flags(safe_only: bool, include_review: bool) -> None:
    """Reject contradictory tier flags.

    Args:
        safe_only: Whether ``--safe-only`` was given.
        include_review: Whether ``--review`` / ``--include-review`` was given.

    Raises:
        click.UsageError: If both were given.
    """
    if safe_only and include_review:
        raise click.UsageError("--safe-only cannot be combined with --review / --include-review.")


def _stdin_is_tty() -> bool:
    """Return True if standard input is an interactive terminal.

    Returns:
        Whether prompts can be shown.
    """
    return sys.stdin.isatty()


def _welcome_marker() -> Path:
    """Return the file recording that the first-run welcome has been handled.

    Returns:
        Path inside the bekas data directory.
    """
    return data_dir() / "welcomed"


def _is_first_run() -> bool:
    """Return True if this looks like the user's first use of bekas.

    Returns:
        True when no command has been run before and no clean has ever been recorded.
    """
    try:
        return not _welcome_marker().exists() and not list_runs()
    except OSError:
        return False


def _mark_welcomed() -> None:
    """Remember that bekas has been used, so the first-run welcome is not repeated."""
    try:
        marker = _welcome_marker()
        if not marker.exists():
            marker.touch()
    except OSError:
        pass


def _audit_to_plan(report: AuditReport, safe_only: bool = False, include_review: bool = False) -> Plan:
    """Convert an AuditReport into a Plan based on confidence filters.

    Args:
        report: Audit report to derive the plan from.
        safe_only: If True, only include SAFE-tier candidates.
        include_review: If True, explicitly include REVIEW-tier candidates
            (ignored when safe_only is True).

    Returns:
        Plan containing the filtered candidates.
    """
    allowed = {Confidence.SAFE}
    if include_review and not safe_only:
        allowed.add(Confidence.REVIEW)
    candidates = [c for pr in report.plugins for c in pr.candidates if c.confidence in allowed]
    return Plan(audit_id=report.audit_id, candidates=candidates)


@click.group(invoke_without_command=True)
@click.option("--profile", default=None, help="Configuration profile to use.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output.")
@click.pass_context
def cli(ctx: click.Context, profile: str | None, json_output: bool, verbose: bool) -> None:
    """bekas — finds leftover stuff on your machine and helps reclaim disk space safely."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["json"] = json_output
    ctx.obj["verbose"] = verbose

    if ctx.invoked_subcommand is not None:
        _mark_welcomed()
        return

    if not _is_first_run():
        click.echo(ctx.get_help())
        return

    # First-run welcome
    click.echo("Welcome to bekas.")
    click.echo("")
    click.echo(
        "bekas finds leftover stuff on your machine — Docker images you forgot, "
        "Python venvs from old projects, old downloads, etc. — and helps you "
        "reclaim disk space safely."
    )
    click.echo("")
    if _stdin_is_tty():
        _mark_welcomed()
        resp = click.prompt("You haven't run an audit yet. Try one now?", default="Y")
        if resp.strip().lower() in ("y", "yes"):
            ctx.invoke(audit_cmd)
        else:
            click.echo("\nNext steps:")
            click.echo("  bekas audit       Run an audit")
            click.echo("  bekas plan        See what would be removed")
            click.echo("  bekas clean       Auto-clean safe items")
    else:
        click.echo("Run 'bekas audit' to get started.")


@cli.command("audit")
@click.option("--plugin", "plugins_filter", default=None, help="Comma-separated plugin names.")
@click.option("--serial", is_flag=True, help="Run plugins serially.")
@click.option("--sort-by", type=click.Choice(["size", "age", "tier"]), default=None, help="Sort candidates by key.")
@click.option(
    "--top",
    type=click.IntRange(min=1),
    default=None,
    help="List at most N candidates per plugin (totals are unaffected).",
)
@click.option("--no-cache", is_flag=True, help="Bypass the audit cache.")
@click.option("--rebuild-cache", is_flag=True, help="Clear and rebuild the audit cache.")
@click.option("--save", "save_name", default=None, help="Persist this audit for later --from-audit (optional NAME).")
@click.option("--min-size", default=None, help="Only show candidates of at least this size (e.g. 500MB).")
@click.option("--older-than", default=None, help="Only show candidates older than this (e.g. 90d).")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json", "ndjson", "csv"]),
    default=None,
    help="Output format (default: table).",
)
@click.option("--notify", is_flag=True, help="Send a desktop notification with the reclaimable total.")
@click.option(
    "--exit-code-above",
    type=str,
    default=None,
    help="Exit with code 4 when reclaimable space exceeds this size (e.g. 5GB).",
)
@click.pass_context
def audit_cmd(
    ctx: click.Context,
    plugins_filter: str | None,
    serial: bool,
    sort_by: str | None,
    top: int | None,
    no_cache: bool,
    rebuild_cache: bool,
    save_name: str | None,
    min_size: str | None,
    older_than: str | None,
    fmt: str | None,
    notify: bool,
    exit_code_above: str | None,
) -> None:
    """Run a read-only audit."""
    profile_name = ctx.obj.get("profile")
    all_plugins = _get_plugins()

    if plugins_filter:
        names = {n.strip() for n in plugins_filter.split(",")}
        all_plugins = [p for p in all_plugins if p.name in names]

    click.echo("Running audit...")
    report = run_audit(
        all_plugins,
        profile_name=profile_name,
        serial=serial,
        no_cache=no_cache,
        rebuild_cache=rebuild_cache,
    )

    min_size_bytes = _parse_filter_arg(min_size, parse_size) if min_size else None
    older_than_secs = _parse_filter_arg(older_than, parse_age) if older_than else None
    if min_size_bytes is not None or older_than_secs is not None:
        for pr in report.plugins:
            pr.candidates = apply_filters(pr.candidates, min_size=min_size_bytes, older_than=older_than_secs)
        report.summary.total_bytes = sum(c.size_bytes for pr in report.plugins for c in pr.candidates)

    fmt_active = fmt or ("json" if ctx.obj.get("json") else None)
    if fmt_active == "ndjson" or fmt_active == "csv":
        click.echo(format_output(report, fmt_active, ""))
    elif fmt_active == "json":
        click.echo(format_json(report))
    else:
        click.echo(format_human(report, sort_by=sort_by, top=top))

    if save_name is not None:
        save_audit(report, name=save_name or None)
        click.echo(f"Audit saved: {report.audit_id}" + (f" (name: {save_name})" if save_name else ""))

    total_bytes = sum(c.size_bytes for pr in report.plugins for c in pr.candidates)

    if notify:
        _send_desktop_notification(f"bekas audit: {_human_size(total_bytes)} reclaimable")

    if exit_code_above:
        try:
            threshold = parse_size(exit_code_above)
        except ValueError as exc:
            raise click.BadParameter(str(exc)) from exc
        if total_bytes > threshold:
            click.echo(
                f"Reclaimable {_human_size(total_bytes)} exceeds threshold {_human_size(threshold)}.",
                err=True,
            )
            sys.exit(4)

    # Save report in context for chaining if needed
    ctx.obj["last_audit"] = report


@cli.command("plan")
@click.option(
    "--safe-only", is_flag=True, help="Only SAFE-tier items (the default; errors if combined with --include-review)."
)
@click.option("--include-review", is_flag=True, help="Also include REVIEW-tier items.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.option("--save", type=click.Path(), default=None, help="Save signed plan to a file.")
@click.option(
    "--top",
    type=click.IntRange(min=1),
    default=None,
    help="List at most N candidates per category (totals are unaffected).",
)
@click.option("--no-cache", is_flag=True, help="Bypass the audit cache.")
@click.option("--from-audit", default=None, help="Reuse a saved audit (id, name, or 'last') instead of rescanning.")
@click.option("--min-size", default=None, help="Only include candidates of at least this size (e.g. 500MB).")
@click.option("--older-than", default=None, help="Only include candidates older than this (e.g. 90d).")
@click.option("--diff", "diff_ref", default=None, help="Diff against a saved plan file (from `plan --save FILE`).")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json", "ndjson", "csv"]),
    default=None,
    help="Output format (default: table).",
)
@click.pass_context
def plan_cmd(
    ctx: click.Context,
    safe_only: bool,
    include_review: bool,
    json_output: bool,
    save: str | None,
    top: int | None,
    no_cache: bool,
    from_audit: str | None,
    min_size: str | None,
    older_than: str | None,
    diff_ref: str | None,
    fmt: str | None,
) -> None:
    """Run a fresh audit and preview what clean would do."""
    _check_tier_flags(safe_only, include_review)
    profile_name = ctx.obj.get("profile")
    min_size_bytes = _parse_filter_arg(min_size, parse_size) if min_size else None
    older_than_secs = _parse_filter_arg(older_than, parse_age) if older_than else None

    if from_audit:
        report = _load_audit_ref(from_audit)
    else:
        all_plugins = _get_plugins()
        report = run_audit(all_plugins, profile_name=profile_name, no_cache=no_cache)
    plan = _audit_to_plan(report, safe_only=safe_only, include_review=include_review)

    if min_size_bytes is not None or older_than_secs is not None:
        plan.candidates = apply_filters(plan.candidates, min_size=min_size_bytes, older_than=older_than_secs)

    if diff_ref:
        old_plan = _load_plan_file(diff_ref, allow_unsigned=True)
        delta = diff_plans(old_plan, plan)
        for key in ("added", "removed", "changed"):
            items = delta[key]
            click.echo(f"\n{key.capitalize()}: {len(items)}")
            for c in items[: top or 20]:
                extra = ""
                if key == "changed":
                    old = next(o for o in old_plan.candidates if o.id == c.id)
                    extra = f" ({_human_size(old.size_bytes)} → {_human_size(c.size_bytes)})"
                click.echo(f"  {c.path_or_handle} [{c.category}] — {_human_size(c.size_bytes)}{extra}")
        return

    if save:
        # Record what each candidate looks like now, so `clean --plan-file` can
        # refuse items that changed since. Both fields are covered by the signature.
        plan.fingerprints = fingerprint_plan(plan)
        plan.signed_at = datetime.now(UTC)
        plan_data = plan.model_dump(mode="json")
        plan_data["_signature"] = sign_plan(plan_data)
        with open(save, "w") as f:
            json.dump(plan_data, f, indent=2)
        click.echo(f"Signed plan saved to {save}")
        return

    use_json = json_output or ctx.obj.get("json")
    if use_json:
        click.echo(format_json(plan))
    else:
        click.echo(format_human(plan, top=top))


def _load_plan_file(plan_file: str, allow_unsigned: bool) -> Plan:
    """Load a saved plan and verify its signature, exiting on any problem.

    Args:
        plan_file: Path of the plan JSON written by ``plan --save``.
        allow_unsigned: Accept a plan without ``_signature``. A plan that has a
            signature is always verified, whatever this flag says.

    Returns:
        The parsed plan.
    """
    try:
        with open(plan_file) as f:
            plan_data = json.load(f)
    except (OSError, ValueError) as exc:
        click.echo(f"Error: could not read plan file: {exc}")
        sys.exit(1)
    if not isinstance(plan_data, dict):
        click.echo("Error: plan file is not a valid plan.")
        sys.exit(1)

    signature = plan_data.pop("_signature", None)
    if signature is None:
        if not allow_unsigned:
            click.echo(
                "Error: plan file has no signature. Re-create it with `bekas plan --save`, "
                "or pass --allow-unsigned if you wrote it yourself."
            )
            sys.exit(1)
    elif not isinstance(signature, str) or not verify_plan(plan_data, signature):
        click.echo("Error: plan file signature is invalid or was tampered with.")
        sys.exit(1)

    try:
        return Plan(**plan_data)
    except ValueError as exc:
        click.echo(f"Error: plan file is not a valid plan: {exc}")
        sys.exit(1)


@cli.command("clean")
@click.option("--apply", is_flag=True, help="Actually delete (default is dry-run).")
@click.option("--safe-only", is_flag=True, help="Only SAFE-tier items (the default; errors if combined with --review).")
@click.option(
    "--review",
    "include_review",
    is_flag=True,
    help="Also include REVIEW-tier items. The typed confirmation still applies.",
)
@click.option("--yes-all", is_flag=True, help="Skip per-category prompts.")
@click.option("--non-interactive", is_flag=True, help="Non-interactive mode (requires --accept-categories).")
@click.option("--accept-categories", default=None, help="Comma-separated categories for non-interactive mode.")
@click.option("--plan-file", type=click.Path(exists=True), default=None, help="Apply a saved JSON plan file.")
@click.option("--force-stale", is_flag=True, help="Apply a plan even if candidates have drifted.")
@click.option("--allow-unsigned", is_flag=True, help="Accept a --plan-file that carries no signature.")
@click.option(
    "--allow-manual",
    is_flag=True,
    default=None,
    help="Allow MANUAL-tier candidates. Default: profile allow_manual setting.",
)
@click.option("--interactive", "interactive", is_flag=True, help="Pick candidates one by one before applying.")
@click.option("--from-audit", default=None, help="Reuse a saved audit (id, name, or 'last') instead of rescanning.")
@click.option("--min-size", default=None, help="Only include candidates of at least this size (e.g. 500MB).")
@click.option("--older-than", default=None, help="Only include candidates older than this (e.g. 90d).")
@click.pass_context
def clean_cmd(
    ctx: click.Context,
    apply: bool,
    safe_only: bool,
    include_review: bool,
    yes_all: bool,
    non_interactive: bool,
    accept_categories: str | None,
    plan_file: str | None,
    force_stale: bool,
    allow_unsigned: bool,
    allow_manual: bool | None,
    interactive: bool,
    from_audit: str | None,
    min_size: str | None,
    older_than: str | None,
) -> None:
    """Remove approved candidates. Dry-run by default."""
    _check_tier_flags(safe_only, include_review)
    if interactive and non_interactive:
        click.echo("Error: --interactive and --non-interactive are mutually exclusive.")
        sys.exit(2)
    if interactive and not _stdin_is_tty():
        click.echo("Error: --interactive requires a terminal (stdin is not a tty).")
        sys.exit(2)
    profile_name = ctx.obj.get("profile")
    profile = profile_for(profile_name)
    min_size_bytes = _parse_filter_arg(min_size, parse_size) if min_size else None
    older_than_secs = _parse_filter_arg(older_than, parse_age) if older_than else None

    # Non-interactive requires yes-all or accept-categories
    if non_interactive and not (yes_all or accept_categories):
        click.echo("Error: --non-interactive requires --yes-all or --accept-categories.")
        sys.exit(2)

    if plan_file:
        plan = _load_plan_file(plan_file, allow_unsigned)
        report = None
    else:
        if from_audit:
            report = _load_audit_ref(from_audit)
        else:
            all_plugins = _get_plugins()
            report = run_audit(all_plugins, profile_name=profile_name)
        plan = _audit_to_plan(report, safe_only=safe_only, include_review=include_review)

    if min_size_bytes is not None or older_than_secs is not None:
        plan.candidates = apply_filters(plan.candidates, min_size=min_size_bytes, older_than=older_than_secs)

    if interactive:
        plan.candidates = select_interactive(plan.candidates)
        if not plan.candidates:
            click.echo("Nothing selected.")
            return

    if not plan.candidates:
        click.echo("Nothing to clean.")
        return

    if not apply:
        click.echo("Dry run mode (default).")
        click.echo("Re-run with --apply to actually delete.")
        click.echo("")
        click.echo(format_human(plan))
        return

    # If non-interactive, we still filter to accepted categories
    if non_interactive and accept_categories:
        accepted = {c.strip() for c in accept_categories.split(",")}
        plan.candidates = [c for c in plan.candidates if c.category.split(".")[0] in accepted]

    ctx_obj = Context(dry_run=False, config=profile.model_dump(), verbose=ctx.obj.get("verbose", False))
    quarantine_enabled = profile.quarantine_enabled
    manual_allowed = allow_manual if allow_manual is not None else profile.allow_manual

    # Acquire single-instance lock for mutating commands
    try:
        with ProcessLock():
            result = apply_plan(
                plan,
                _get_plugins(),
                ctx_obj,
                audit=report,
                yes_all=yes_all or non_interactive,
                quarantine_enabled=quarantine_enabled,
                profile_name=profile_name,
                non_interactive=non_interactive,
                force_stale=force_stale,
                allow_manual=manual_allowed,
            )
    except AlreadyRunningError as exc:
        click.echo(str(exc))
        sys.exit(3)
    except CleanAborted as exc:
        click.echo(str(exc))
        sys.exit(exc.exit_code)

    if ctx.obj.get("json"):
        click.echo(format_json(result))
    else:
        click.echo(format_human(result))


@cli.command("inspect")
@click.argument("candidate_id")
@click.option("--from-audit", default=None, help="Search a saved audit (id, name, or 'last') instead of rescanning.")
@click.pass_context
def inspect_cmd(ctx: click.Context, candidate_id: str, from_audit: str | None) -> None:
    """Show full reasoning for a single candidate (fresh audit or a saved one)."""
    if from_audit:
        report = _load_audit_ref(from_audit)
    else:
        all_plugins = _get_plugins()
        report = run_audit(all_plugins, profile_name=ctx.obj.get("profile"))
    for pr in report.plugins:
        for c in pr.candidates:
            if c.id == candidate_id:
                click.echo(f"Candidate: {c.id}")
                click.echo(f"Category:  {c.category}")
                click.echo(f"Size:      {_human_size(c.size_bytes)}")
                click.echo(f"Confidence: {c.confidence.value}")
                click.echo(f"Reason:    {c.reason}")
                click.echo(f"Path:      {c.path_or_handle}")
                if c.metadata:
                    click.echo("Metadata:")
                    for k, v in c.metadata.items():
                        click.echo(f"  {k}: {v}")
                return
    click.echo(f"Candidate {candidate_id} not found. `inspect` runs a fresh audit; it may no longer exist.")


@cli.command("history")
@click.argument("run_id", required=False)
@click.option("--since", type=int, default=None, help="Only show events from the last N hours.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.option("--report", type=click.Path(writable=True), default=None, help="Write a markdown report of a run.")
@click.pass_context
def history_cmd(
    ctx: click.Context, run_id: str | None, since: int | None, json_output: bool, report: str | None
) -> None:
    """List past actions or show details of a specific run."""
    if run_id:
        run = get_run(run_id)
        if not run:
            click.echo(f"Run {run_id} not found.")
            return
        if report:
            path = _write_run_report(run, report)
            click.echo(f"Report written to {path}")
            return
        click.echo(f"Run {run['run_id']} at {run['timestamp']}")
        click.echo(f"Audit ID: {run['audit_id']}")
        click.echo(f"Total freed: {_human_size(run['total_bytes_freed'])}")
        return

    if report:
        click.echo("Error: --report requires a RUN_ID.")
        sys.exit(2)
    events = read_events(since_hours=since)
    if not events:
        click.echo("No history yet.")
        return

    if json_output:
        click.echo(json.dumps(events, indent=2))
        return

    click.echo(f"{'Run ID':18s} {'Time':20s} {'Event':18s} {'Items':>6s} {'Freed':>12s}")
    for ev in events[:50]:
        click.echo(
            f"{ev.get('run_id', ''):18s} {ev.get('ts', ''):20s} {ev.get('event', ''):18s} "
            f"{ev.get('items_total', 0):>6d} {_human_size(ev.get('bytes_reclaimed', 0)):>12s}"
        )


@cli.command("undo")
@click.argument("run_id", required=False)
@click.option("--list", "list_items", is_flag=True, help="List the items of a run with their undo state.")
@click.option("--item", "item_id", default=None, help="Restore only this candidate id from the run.")
@click.pass_context
def undo_cmd(ctx: click.Context, run_id: str | None, list_items: bool, item_id: str | None) -> None:
    """Undo the last apply or a specific run (optionally a single item)."""
    if not run_id:
        runs = list_runs()
        if not runs:
            click.echo("No runs to undo.")
            return
        run_id = runs[0]["run_id"]

    if list_items:
        rows = undo_item_ids(run_id)
        if not rows:
            click.echo(f"Run {run_id} not found or has no items.")
            return
        click.echo(f"{'Candidate':34s} {'Category':26s} {'Size':>10s} {'State':10s}")
        for row in rows:
            click.echo(
                f"{str(row['candidate_id']):34s} {str(row['category']):26s} "
                f"{_human_size(int(row['size_bytes'])):>10s} {row['state']:10s}"
            )
        return

    if item_id:
        run = get_run(run_id)
        if not run:
            click.echo(f"Run {run_id} not found.")
            return
        try:
            run_results = json.loads(run["results_json"])
        except Exception:
            click.echo("Could not parse run results.")
            return
        if item_id not in {e.get("candidate_id") for e in run_results}:
            click.echo(f"Candidate {item_id} is not part of run {run_id}. See `bekas undo {run_id} --list`.")
            sys.exit(2)
        plan_candidates = _load_plan_candidates(run)
        click.echo(f"Undo item {item_id} of run {run_id}...")
        with _exclusive():
            fresh = get_run(run_id)
            outcome = undo_run_item(
                run_id,
                json.loads(fresh["results_json"]) if fresh else run_results,
                item_id,
                candidates=plan_candidates,
                plugins=_get_plugins(),
            )
        _echo_undo_outcome(outcome)
        return

    run = get_run(run_id)
    if not run:
        click.echo(f"Run {run_id} not found.")
        return

    try:
        results = json.loads(run["results_json"])
    except Exception:
        click.echo("Could not parse run results.")
        return

    plan_candidates = _load_plan_candidates(run)
    click.echo(f"Undo run {run_id}...")
    with _exclusive():
        # Re-read under the lock: a concurrent undo may have restored items meanwhile.
        fresh = get_run(run_id)
        if fresh:
            results = json.loads(fresh["results_json"])
        outcome = undo_run_results(run_id, results, candidates=plan_candidates, plugins=_get_plugins())
    _echo_undo_outcome(outcome)


def _load_plan_candidates(run: dict[str, Any]) -> list[Candidate]:
    """Parse the plan candidates stored with a run, for plugin-based undo."""
    try:
        plan_data = json.loads(run.get("plan_json") or "{}")
        return Plan(
            **{"audit_id": plan_data.get("audit_id", ""), "candidates": plan_data.get("candidates", [])}
        ).candidates
    except (ValueError, TypeError):
        return []


def _write_run_report(run: dict[str, Any], report_path: str) -> Path:
    """Write a human-readable markdown report of one run.

    Args:
        run: Run row from the runs database.
        report_path: Destination file for the markdown.

    Returns:
        The path written.
    """
    candidates = {c.id: c for c in _load_plan_candidates(run)}
    try:
        results = json.loads(run["results_json"])
    except (ValueError, TypeError):
        results = []
    outcome = undo_item_ids(run["run_id"])
    state_by_id = {row["candidate_id"]: row["state"] for row in outcome}

    lines: list[str] = [
        f"# bekas run {run['run_id']}",
        "",
        f"- Timestamp: {run['timestamp']}",
        f"- Audit ID: `{run['audit_id']}`",
        f"- Total freed: {_human_size(run['total_bytes_freed'])}",
        "",
        "## Items",
        "",
        "| Candidate | Category | Size | State | Reason |",
        "|---|---|---:|---|---|",
    ]
    for entry in results:
        cid = entry.get("candidate_id", "")
        c = candidates.get(cid)
        name = c.path_or_handle if c else cid
        category = c.category if c else (entry.get("log") or "?")
        reason = ""
        if c is not None:
            reason = (c.reason or "").replace("|", "\\|")
        lines.append(
            f"| `{name}` | {category} | {_human_size(entry.get('bytes_freed', 0))} | "
            f"{state_by_id.get(cid, 'unknown')} | {reason} |"
        )
    restored = sum(1 for row in outcome if row["state"] == "restored")
    lines += [
        "",
        "## Summary",
        "",
        f"- Candidates acted on: {len(results)}",
        f"- Restored by a later undo: {restored}",
        f"- Deleted permanently: {sum(1 for s in state_by_id.values() if s == 'permanent')}",
        f"- Still restorable: {sum(1 for s in state_by_id.values() if s == 'restorable')}",
        "",
    ]
    path = Path(report_path)
    path.write_text("\n".join(lines))
    return path


def _echo_undo_outcome(outcome: UndoOutcome) -> None:
    """Print an undo summary and exit non-zero on any failure."""
    for token, reason in outcome.failed:
        click.echo(f"  Failed to restore {token}: {reason}")
    if outcome.restored == 0 and not outcome.failed and not outcome.already_restored:
        click.echo("No quarantined items to restore for this run.")
    else:
        click.echo(f"Restored {outcome.restored} item(s). Failed: {len(outcome.failed)}.")
    if outcome.already_restored:
        click.echo(f"{outcome.already_restored} item(s) were already restored by an earlier undo.")
    if outcome.permanent:
        click.echo(f"{outcome.permanent} item(s) were deleted permanently (not quarantined) and cannot be restored.")
    if outcome.failed:
        sys.exit(1)


@cli.group("quarantine")
def quarantine_group() -> None:
    """Manage quarantined items."""
    pass


@quarantine_group.command("list")
def quarantine_list() -> None:
    """List quarantined items."""
    items = list_quarantine()
    if not items:
        click.echo("Quarantine is empty.")
        return
    click.echo(f"{'ID':14s} {'Category':20s} {'Original':40s} {'Size':>12s}")
    for item in items:
        click.echo(
            f"{item['quarantine_id']:14s} {item['category']:20s} "
            f"{item['original_path']:40s} {_human_size(item['size_bytes']):>12s}"
        )


@quarantine_group.command("restore")
@click.argument("quarantine_id")
def quarantine_restore(quarantine_id: str) -> None:
    """Restore a quarantined item to its original location."""
    with _exclusive():
        try:
            dest = restore_from_quarantine(quarantine_id)
        except Exception as exc:
            click.echo(f"Restore failed: {exc}")
            sys.exit(1)
    click.echo(f"Restored to {dest}")


@quarantine_group.command("purge")
def quarantine_purge() -> None:
    """Empty quarantine immediately."""
    with _exclusive():
        removed, freed = purge_old_quarantine(retention_days=0)
    click.echo(f"Purged {removed} items, freeing {_human_size(freed)}.")


@cli.group("plugins")
def plugins_group() -> None:
    """Manage plugins."""
    pass


@plugins_group.command("new")
@click.argument("name")
@click.option("--target", type=click.Path(file_okay=False), default=".", help="Directory to scaffold the package in.")
def plugins_new(name: str, target: str) -> None:
    """Scaffold a third-party plugin package (bundled plugin SDK)."""
    from bekas.pluginsdk import API_VERSION, scaffold_plugin

    try:
        pkg_dir = scaffold_plugin(name, Path(target))
    except ValueError as exc:
        click.echo(f"Error: {exc}")
        sys.exit(1)
    click.echo(f"Plugin package scaffolded in {pkg_dir}")
    click.echo("Next steps:")
    click.echo("  1. edit the discover()/remove() logic in the generated plugin module")
    click.echo("  2. pip install -e " + str(pkg_dir))
    click.echo("  3. bekas plugins list  → verify the plugin appears")
    click.echo(f"Plugins declare __bekas_api_version__ = {API_VERSION}; mismatched versions refuse to load.")


@cli.group("completion")
def completion_group() -> None:
    """Print shell completion scripts."""


_BASH_COMPLETION = """# bash completion for bekas
_bekas_completion() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local cmds="audit plan clean inspect history undo quarantine plugins doctor config tui completion version"
    local opts="--apply --safe-only --include-review --review --yes-all --non-interactive \
--accept-categories --plan-file --force-stale --allow-unsigned --allow-manual --interactive \
--from-audit --min-size --older-than --format --save --diff --json --verbose --serial \
--sort-by --top --no-cache --rebuild-cache --plugin --list --item --skip --fix --dry-run \
--notify --exit-code-above --target"
    case "$cur" in
        --*) COMPREPLY=( $(compgen -W "$opts" -- "$cur") ) ;;
        *)   COMPREPLY=( $(compgen -W "$cmds" -- "$cur") ) ;;
    esac
}
complete -F _bekas_completion -o default bekas
"""

_ZSH_COMPLETION = """#compdef bekas
# zsh completion for bekas
_bekas() {
    local -a cmds opts
    cmds=(audit plan clean inspect history undo quarantine plugins doctor config tui completion version)
    opts=(--apply --safe-only --include-review --review --yes-all --non-interactive \
--accept-categories --plan-file --force-stale --allow-unsigned --allow-manual --interactive \
--from-audit --min-size --older-than --format --save --diff --json --verbose --serial \
--sort-by --top --no-cache --rebuild-cache --plugin --list --item --skip --fix --dry-run \
--notify --exit-code-above --target)
    if [[ "$CURRENT" -ge 2 && "${words[2]}" != -* ]]; then
        _describe 'command' cmds
    else
        _describe 'option' opts
    fi
}
compdef _bekas bekas
"""

_FISH_COMPLETION = """# fish completion for bekas
complete -c bekas -f
complete -c bekas -n "__fish_use_subcommand" -a "audit plan clean inspect history undo quarantine \\
plugins doctor config tui completion version"
complete -c bekas -l apply -d "Actually delete"
complete -c bekas -l safe-only -d "Only SAFE-tier items"
complete -c bekas -l include-review -d "Include REVIEW-tier items"
complete -c bekas -l yes-all -d "Skip prompts"
complete -c bekas -l non-interactive -d "Non-interactive mode"
complete -c bekas -l accept-categories -d "Category whitelist" -x
complete -c bekas -l plan-file -d "Saved plan file" -r
complete -c bekas -l allow-manual -d "Allow MANUAL-tier candidates"
complete -c bekas -l interactive -d "Pick candidates one by one"
complete -c bekas -l from-audit -d "Reuse saved audit" -x
complete -c bekas -l min-size -d "Minimum size" -x
complete -c bekas -l older-than -d "Minimum age" -x
complete -c bekas -l format -d "Output format" -x
complete -c bekas -l save -d "Persist audit/plan" -x
complete -c bekas -l diff -d "Diff vs saved plan" -r
complete -c bekas -l json -d "JSON output"
complete -c bekas -l verbose -d "Verbose output"
complete -c bekas -l notify -d "Send desktop notification"
complete -c bekas -l exit-code-above -d "Exit non-zero above size threshold" -x
"""


@completion_group.command("bash")
def completion_bash() -> None:
    """Bash completion script (source it: source <(bekas completion bash))."""
    click.echo(_BASH_COMPLETION)


@completion_group.command("zsh")
def completion_zsh() -> None:
    """Zsh completion script (add to fpath)."""
    click.echo(_ZSH_COMPLETION)


@completion_group.command("fish")
def completion_fish() -> None:
    """Fish completion script."""
    click.echo(_FISH_COMPLETION)


@cli.command("version")
def version_cmd() -> None:
    """Print the bekas version."""
    from bekas import __version__

    click.echo(f"bekas {__version__}")


@plugins_group.command("list")
@click.pass_context
def plugins_list(ctx: click.Context) -> None:
    """List installed plugins and their capabilities."""
    profile_name = ctx.obj.get("profile")
    profile = profile_for(profile_name)
    patterns = profile.enabled_plugins
    all_plugins = _get_plugins()
    click.echo(f"{'Plugin':30s} {'Available':10s} {'Enabled':10s} {'Quar':6s} {'Undo':6s} {'Runtime':8s}")
    for p in all_plugins:
        avail = "yes" if p.is_available(Context(dry_run=True, config=profile.model_dump())) else "no"
        enabled = "yes" if is_plugin_enabled(patterns, p.name) else "no"
        quar = "yes" if p.capabilities.quarantine else "no"
        undo = "yes" if p.supports_undo() else "no"
        runtime = p.capabilities.estimated_runtime
        click.echo(f"{p.name:30s} {avail:10s} {enabled:10s} {quar:6s} {undo:6s} {runtime:8s}")
        click.echo(f"  {p.description}")
        if p.capabilities.requires_cli:
            click.echo(f"  requires: {', '.join(p.capabilities.requires_cli)}")


@plugins_group.command("enable")
@click.argument("plugin_name")
@click.pass_context
def plugins_enable(ctx: click.Context, plugin_name: str) -> None:
    """Enable a plugin in the active profile of the config file."""
    from bekas.config import set_plugin_enabled

    names = [p.name for p in _get_plugins()]
    ok, msg = set_plugin_enabled(plugin_name, True, names, ctx.obj.get("profile"))
    click.echo(msg)
    if not ok and plugin_name not in names:
        sys.exit(1)


@plugins_group.command("disable")
@click.argument("plugin_name")
@click.pass_context
def plugins_disable(ctx: click.Context, plugin_name: str) -> None:
    """Disable a plugin in the active profile of the config file."""
    from bekas.config import set_plugin_enabled

    names = [p.name for p in _get_plugins()]
    ok, msg = set_plugin_enabled(plugin_name, False, names, ctx.obj.get("profile"))
    click.echo(msg)
    if not ok and plugin_name not in names:
        sys.exit(1)


@cli.command("doctor")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.option("--skip", default=None, help="Comma-separated checks to skip.")
@click.option(
    "--fix",
    is_flag=True,
    help="Repair what is safe to repair: recreate missing data dirs, tighten permissions, drop corrupt run rows.",
)
@click.option("--dry-run", is_flag=True, help="With --fix: list what would change without changing anything.")
def doctor_cmd(json_output: bool, skip: str | None, fix: bool, dry_run: bool) -> None:
    """Diagnose the bekas runtime environment."""
    from bekas.doctor import collect_fixes, exit_code, format_human, format_json, run_checks

    if fix:
        actions, problems = collect_fixes(dry_run=dry_run)
        if json_output:
            click.echo(
                json.dumps(
                    {
                        "dry_run": dry_run,
                        "actions": [{"name": a.name, "detail": a.detail} for a in actions],
                        "problems": problems,
                    },
                    indent=2,
                )
            )
        else:
            click.echo("bekas doctor --fix" + (" (dry run)" if dry_run else ""))
            if actions:
                for a in actions:
                    click.echo(f"  + {a.name}: {a.detail}")
            else:
                click.echo("  Nothing to fix.")
            if problems:
                click.echo("  Problems found:")
                for p in problems:
                    click.echo(f"  ⚠ {p}")
        return

    skip_list = [s.strip() for s in (skip or "").split(",") if s.strip()]
    results = run_checks(skip=skip_list)
    if json_output:
        click.echo(format_json(results))
    else:
        click.echo("bekas doctor")
        click.echo(format_human(results))
    sys.exit(exit_code(results))


@cli.group("config", invoke_without_command=True)
@click.pass_context
def config_group(ctx: click.Context) -> None:
    """Configuration management."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(config_show, resolved=False)


@config_group.command("show")
@click.option("--resolved", is_flag=True, help="Show effective config after profile merge.")
@click.pass_context
def config_show(ctx: click.Context, resolved: bool) -> None:
    """Print current effective configuration."""
    click.echo(f"Config file: {config_path()}")
    click.echo("")
    if resolved:
        effective = resolved_config(ctx.obj.get("profile"))
        click.echo(json.dumps(effective, indent=2, default=str))
    else:
        cfg = load_config()
        click.echo(json.dumps(cfg.model_dump(), indent=2, default=str))


@config_group.command("validate")
def config_validate() -> None:
    """Validate the current configuration file."""
    ok, msg = validate_config_file()
    if ok:
        click.echo(msg)
    else:
        click.echo(msg)
        sys.exit(1)


@cli.command("tui")
def tui_cmd() -> None:
    """Launch interactive TUI (Textual)."""
    try:
        from bekas.tui import TuiApp

        app = TuiApp()
        app.run()
    except Exception as exc:
        click.echo(f"TUI not available: {exc}")
        sys.exit(1)


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


def main() -> None:
    """Application entry point. Ensures config exists and starts the CLI."""
    ensure_config()
    cli()


if __name__ == "__main__":
    main()
