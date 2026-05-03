"""Audit runner — orchestrates plugins to collect candidates."""

from __future__ import annotations

import contextlib
import platform
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from bekas.config import is_plugin_enabled, load_config, profile_for
from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, Context, PluginReport, SystemInfo
from bekas.plugin import Plugin
from bekas.safety import filter_candidates


def _run_plugin(plugin: Plugin, ctx: Context) -> PluginReport:
    """Run a single plugin and collect discovered candidates.

    Exceptions raised during discovery are swallowed so that a crashing
    plugin does not abort the entire audit.

    Args:
        plugin: Plugin instance to run.
        ctx: Execution context passed to the plugin.

    Returns:
        PluginReport containing discovered candidates and the raw count.
    """
    candidates: list[Candidate] = []
    try:
        for c in plugin.discover(ctx):
            candidates.append(c)
    except Exception:
        # A crashing plugin must not crash the audit
        pass
    return PluginReport(name=plugin.name, candidates_found=len(candidates), candidates=candidates)


def run_audit(
    plugins: list[Plugin],
    ctx: Context | None = None,
    profile_name: str | None = None,
    serial: bool = False,
) -> AuditReport:
    """Run an audit across all enabled and available plugins.

    Discovers candidates, filters them through safety exclusions, and
    produces a summary with system information.

    Args:
        plugins: List of plugin instances to consider.
        ctx: Optional execution context; a default dry-run context is
            created if not provided.
        profile_name: Optional configuration profile name.
        serial: If True, run plugins sequentially instead of in a thread pool.

    Returns:
        Complete AuditReport with per-plugin results and aggregated summary.
    """
    cfg = load_config()
    profile = profile_for(profile_name)
    patterns = profile.get("enabled_plugins", ["*"])
    user_exclusions = cfg.get("exclude", [])

    ctx = ctx or Context(dry_run=True, config=profile)
    enabled: list[Plugin] = []
    for p in plugins:
        if is_plugin_enabled(patterns, p.name) and p.is_available(ctx):
            enabled.append(p)

    started = datetime.now(UTC)
    started_ts = time.time()
    reports: list[PluginReport] = []

    if serial:
        for p in enabled:
            reports.append(_run_plugin(p, ctx))
    else:
        with ThreadPoolExecutor(max_workers=min(16, len(enabled) or 1)) as ex:
            futures = {ex.submit(_run_plugin, p, ctx): p for p in enabled}
            for fut in as_completed(futures):
                with contextlib.suppress(Exception):
                    reports.append(fut.result())

    # Filter excluded candidates
    for r in reports:
        r.candidates = filter_candidates(r.candidates, user_exclusions)

    duration_ms = int((time.time() - started_ts) * 1000)
    total_candidates = sum(len(pr.candidates) for pr in reports)
    total_bytes = sum(c.size_bytes for pr in reports for c in pr.candidates)
    by_confidence: dict[str, dict[str, int]] = {}
    for conf in Confidence:
        items = [c for pr in reports for c in pr.candidates if c.confidence == conf]
        by_confidence[conf.value] = {"count": len(items), "bytes": sum(c.size_bytes for c in items)}

    system = SystemInfo(
        os=platform.system().lower(),
        arch=platform.machine().lower(),
        free_disk_bytes=shutil.disk_usage(".").free,
    )

    return AuditReport(
        audit_id=f"ad_{uuid.uuid4().hex[:8]}",
        started_at=started,
        duration_ms=duration_ms,
        system=system,
        plugins=reports,
        summary=AuditSummary(
            total_candidates=total_candidates,
            total_bytes=total_bytes,
            by_confidence=by_confidence,
        ),
    )
