"""CLI entry point for bekas."""

from __future__ import annotations

import json
import sys

import click

from bekas.clean import apply_plan
from bekas.config import (
    config_path,
    ensure_config,
    is_plugin_enabled,
    load_config,
    profile_for,
    resolved_config,
    validate_config_file,
)
from bekas.database import get_run, list_quarantine, list_runs
from bekas.events import read_events
from bekas.formatters import format_human, format_json
from bekas.locking import AlreadyRunningError
from bekas.models import AuditReport, Confidence, Context, Plan
from bekas.plugin import Plugin, discover_plugins
from bekas.quarantine import purge_old_quarantine, restore_from_quarantine
from bekas.runner import run_audit
from bekas.signing import sign_plan, verify_plan


def _get_plugins() -> list[Plugin]:
    """Discover and return all installed plugins.

    Returns:
        List of plugin instances.
    """
    return discover_plugins()


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
    if include_review:
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

    if ctx.invoked_subcommand is None:
        # First-run welcome
        click.echo("Welcome to bekas.")
        click.echo("")
        click.echo(
            "bekas finds leftover stuff on your machine — Docker images you forgot, "
            "Python venvs from old projects, old downloads, etc. — and helps you "
            "reclaim disk space safely."
        )
        click.echo("")
        if sys.stdin.isatty():
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
@click.option("--top", type=int, default=None, help="Only show top N candidates per plugin.")
@click.option("--no-cache", is_flag=True, help="Bypass the audit cache.")
@click.option("--rebuild-cache", is_flag=True, help="Clear and rebuild the audit cache.")
@click.pass_context
def audit_cmd(
    ctx: click.Context,
    plugins_filter: str | None,
    serial: bool,
    sort_by: str | None,
    top: int | None,
    no_cache: bool,
    rebuild_cache: bool,
) -> None:
    """Run a read-only audit."""
    profile_name = ctx.obj.get("profile")
    all_plugins = _get_plugins()

    if plugins_filter:
        names = {n.strip() for n in plugins_filter.split(",")}
        all_plugins = [p for p in all_plugins if p.name in names]

    click.echo("Running audit (this may take 30–90 seconds)...")
    report = run_audit(
        all_plugins,
        profile_name=profile_name,
        serial=serial,
        no_cache=no_cache,
        rebuild_cache=rebuild_cache,
    )

    if ctx.obj.get("json"):
        click.echo(format_json(report))
    else:
        click.echo(format_human(report, sort_by=sort_by, top=top))

    # Save report in context for chaining if needed
    ctx.obj["last_audit"] = report


@cli.command("plan")
@click.option("--safe-only", is_flag=True, help="Only include safe-tier items.")
@click.option("--include-review", is_flag=True, help="Include review-tier items.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.option("--save", type=click.Path(), default=None, help="Save signed plan to a file.")
@click.option("--top", type=int, default=None, help="Only show top N candidates.")
@click.option("--no-cache", is_flag=True, help="Bypass the audit cache.")
@click.pass_context
def plan_cmd(
    ctx: click.Context,
    safe_only: bool,
    include_review: bool,
    json_output: bool,
    save: str | None,
    top: int | None,
    no_cache: bool,
) -> None:
    """Preview what clean would do."""
    profile_name = ctx.obj.get("profile")
    all_plugins = _get_plugins()
    report = run_audit(all_plugins, profile_name=profile_name, no_cache=no_cache)
    plan = _audit_to_plan(report, safe_only=safe_only, include_review=include_review)

    if save:
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


@cli.command("clean")
@click.option("--apply", is_flag=True, help="Actually delete (default is dry-run).")
@click.option("--safe-only", is_flag=True, help="Only clean safe-tier items.")
@click.option("--review", "include_review", is_flag=True, help="Include review-tier items interactively.")
@click.option("--yes-all", is_flag=True, help="Skip per-category prompts.")
@click.option("--non-interactive", is_flag=True, help="Non-interactive mode (requires --accept-categories).")
@click.option("--accept-categories", default=None, help="Comma-separated categories for non-interactive mode.")
@click.option("--plan-file", type=click.Path(exists=True), default=None, help="Apply a saved JSON plan file.")
@click.option("--force-stale", is_flag=True, help="Apply a plan even if candidates have drifted.")
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
) -> None:
    """Remove approved candidates. Dry-run by default."""
    profile_name = ctx.obj.get("profile")
    profile = profile_for(profile_name)

    # Non-interactive requires yes-all or accept-categories
    if non_interactive and not (yes_all or accept_categories):
        click.echo("Error: --non-interactive requires --yes-all or --accept-categories.")
        sys.exit(2)

    if plan_file:
        with open(plan_file) as f:
            plan_data = json.load(f)
        signature = plan_data.pop("_signature", None)
        if signature and not verify_plan(plan_data, signature):
            click.echo("Error: plan file signature is invalid or was tampered with.")
            sys.exit(1)
        plan = Plan(**plan_data)
        report = None
    else:
        all_plugins = _get_plugins()
        report = run_audit(all_plugins, profile_name=profile_name)
        plan = _audit_to_plan(report, safe_only=safe_only, include_review=include_review)

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

    # Acquire single-instance lock for mutating commands
    from bekas.locking import acquire_lock

    try:
        lock = acquire_lock()
    except AlreadyRunningError as exc:
        click.echo(str(exc))
        sys.exit(3)

    with lock:
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
        )

    if ctx.obj.get("json"):
        click.echo(format_json(result))
    else:
        click.echo(format_human(result))


@cli.command("inspect")
@click.argument("candidate_id")
@click.pass_context
def inspect_cmd(ctx: click.Context, candidate_id: str) -> None:
    """Show full reasoning for a single candidate."""
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
    click.echo(f"Candidate {candidate_id} not found in latest audit.")


@cli.command("history")
@click.argument("run_id", required=False)
@click.option("--since", type=int, default=None, help="Only show events from the last N hours.")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.pass_context
def history_cmd(ctx: click.Context, run_id: str | None, since: int | None, json_output: bool) -> None:
    """List past actions or show details of a specific run."""
    if run_id:
        run = get_run(run_id)
        if not run:
            click.echo(f"Run {run_id} not found.")
            return
        click.echo(f"Run {run['run_id']} at {run['timestamp']}")
        click.echo(f"Audit ID: {run['audit_id']}")
        click.echo(f"Total freed: {_human_size(run['total_bytes_freed'])}")
        return

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
            f"{ev.get('run_id',''):18s} {ev.get('ts',''):20s} {ev.get('event',''):18s} "
            f"{ev.get('items_total',0):>6d} {_human_size(ev.get('bytes_reclaimed',0)):>12s}"
        )


@cli.command("undo")
@click.argument("run_id", required=False)
@click.pass_context
def undo_cmd(ctx: click.Context, run_id: str | None) -> None:
    """Undo the last apply or a specific run."""
    from bekas.database import get_run
    from bekas.quarantine import restore_from_quarantine

    if not run_id:
        runs = list_runs()
        if not runs:
            click.echo("No runs to undo.")
            return
        run_id = runs[0]["run_id"]

    run = get_run(run_id)
    if not run:
        click.echo(f"Run {run_id} not found.")
        return

    click.echo(f"Undo run {run_id}...")

    import json

    restored = 0
    failed = 0
    try:
        results = json.loads(run["results_json"])
    except Exception:
        click.echo("Could not parse run results.")
        return

    for entry in results:
        undo_token = entry.get("undo_token")
        if not undo_token:
            continue
        try:
            restore_from_quarantine(undo_token)
            restored += 1
        except Exception as exc:
            click.echo(f"  Failed to restore {undo_token}: {exc}")
            failed += 1

    if restored == 0 and failed == 0:
        click.echo("No quarantined items to restore for this run.")
    else:
        click.echo(f"Restored {restored} item(s). Failed: {failed}.")


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
    try:
        dest = restore_from_quarantine(quarantine_id)
        click.echo(f"Restored to {dest}")
    except Exception as exc:
        click.echo(f"Restore failed: {exc}")


@quarantine_group.command("purge")
def quarantine_purge() -> None:
    """Empty quarantine immediately."""
    removed, freed = purge_old_quarantine(retention_days=0)
    click.echo(f"Purged {removed} items, freeing {_human_size(freed)}.")


@cli.group("plugins")
def plugins_group() -> None:
    """Manage plugins."""
    pass


@plugins_group.command("list")
@click.pass_context
def plugins_list(ctx: click.Context) -> None:
    """List installed plugins and their capabilities."""
    profile_name = ctx.obj.get("profile")
    profile = profile_for(profile_name)
    patterns = profile.enabled_plugins
    all_plugins = _get_plugins()
    click.echo(f"{'Plugin':30s} {'Available':10s} {'Enabled':10s} {'Quar':6s} {'Runtime':8s}")
    for p in all_plugins:
        avail = "yes" if p.is_available(Context(dry_run=True, config=profile.model_dump())) else "no"
        enabled = "yes" if is_plugin_enabled(patterns, p.name) else "no"
        quar = "yes" if p.capabilities.quarantine else "no"
        runtime = p.capabilities.estimated_runtime
        click.echo(f"{p.name:30s} {avail:10s} {enabled:10s} {quar:6s} {runtime:8s}")
        click.echo(f"  {p.description}")
        if p.capabilities.requires_cli:
            click.echo(f"  requires: {', '.join(p.capabilities.requires_cli)}")


@plugins_group.command("enable")
@click.argument("plugin_name")
def plugins_enable(plugin_name: str) -> None:
    """Enable a plugin in config (not implemented in v0.1)."""
    click.echo(f"Enable {plugin_name}: edit {config_path()} to add to enabled_plugins.")


@plugins_group.command("disable")
@click.argument("plugin_name")
def plugins_disable(plugin_name: str) -> None:
    """Disable a plugin in config (not implemented in v0.1)."""
    click.echo(f"Disable {plugin_name}: edit {config_path()} to remove from enabled_plugins.")


@cli.command("doctor")
@click.option("--json", "json_output", is_flag=True, help="Output JSON.")
@click.option("--skip", default=None, help="Comma-separated checks to skip.")
def doctor_cmd(json_output: bool, skip: str | None) -> None:
    """Diagnose the bekas runtime environment."""
    from bekas.doctor import exit_code, format_human, format_json, run_checks

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
