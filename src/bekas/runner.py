"""Audit runner orchestrates plugins to collect candidates."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from bekas.config import is_plugin_enabled, load_config, profile_for
from bekas.database import _init_db, get_audit_cache, prune_audit_cache, set_audit_cache
from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, Context, PluginReport, SystemInfo
from bekas.plugin import Plugin
from bekas.safety import filter_candidates


def _fingerprint(path: str) -> str:
    """Compute a cheap filesystem fingerprint for caching.

    Args:
        path: Filesystem path to fingerprint.

    Returns:
        Hash string of size + mtime, or empty string if unavailable.
    """
    try:
        st = os.stat(path)
        data = json.dumps({"size": st.st_size, "mtime": int(st.st_mtime)}, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    except OSError:
        return ""


def _cached_discover(plugin: Plugin, ctx: Context, no_cache: bool = False) -> list[Candidate]:
    """Run plugin discovery with optional audit caching.

    Re-uses a single SQLite connection per plugin to avoid N+1
    connection overhead when scanning many candidates.

    Args:
        plugin: Plugin instance.
        ctx: Execution context.
        no_cache: If True, skip cache reads/writes entirely.

    Returns:
        List of discovered candidates.
    """
    if no_cache:
        return list(plugin.discover(ctx))

    candidates: list[Candidate] = []
    conn = _init_db()
    try:
        for c in plugin.discover(ctx):
            path = c.path_or_handle
            fp = _fingerprint(path)
            if fp:
                cached = get_audit_cache(plugin.name, path, conn=conn)
                if cached and cached.get("fingerprint") == fp:
                    # Rehydrate from cache
                    try:
                        cached_candidate = Candidate.model_validate_json(cached["candidate_json"])
                        candidates.append(cached_candidate)
                        continue
                    except Exception:
                        pass
                set_audit_cache(plugin.name, path, fp, c, conn=conn)
            candidates.append(c)
        conn.commit()
    finally:
        conn.close()
    return candidates


def _run_plugin(plugin: Plugin, ctx: Context, no_cache: bool = False) -> PluginReport:
    """Run a single plugin and collect discovered candidates.

    Exceptions raised during discovery are swallowed so that a crashing
    plugin does not abort the entire audit.

    Args:
        plugin: Plugin instance to run.
        ctx: Execution context passed to the plugin.
        no_cache: If True, skip audit cache reads/writes.

    Returns:
        PluginReport containing discovered candidates and the raw count.
    """
    candidates: list[Candidate] = []
    try:
        candidates = _cached_discover(plugin, ctx, no_cache=no_cache)
    except Exception:
        # A crashing plugin must not crash the audit
        pass
    return PluginReport(name=plugin.name, candidates_found=len(candidates), candidates=candidates)


def run_audit(
    plugins: list[Plugin],
    ctx: Context | None = None,
    profile_name: str | None = None,
    serial: bool = False,
    plugin_timeout_seconds: int = 60,
    no_cache: bool = False,
    rebuild_cache: bool = False,
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
        plugin_timeout_seconds: Hard timeout per plugin in seconds.
        no_cache: If True, bypass the audit cache entirely.
        rebuild_cache: If True, clear and rebuild the audit cache.

    Returns:
        Complete AuditReport with per-plugin results and aggregated summary.
    """
    cfg = load_config()
    profile = profile_for(profile_name)
    patterns = profile.enabled_plugins
    user_exclusions = cfg.exclude
    timeout = profile.plugin_timeout_seconds or plugin_timeout_seconds

    if rebuild_cache:
        from bekas.database import clear_audit_cache

        clear_audit_cache()

    prune_audit_cache()

    ctx = ctx or Context(dry_run=True, config=profile.model_dump())
    enabled: list[Plugin] = []
    for p in plugins:
        if is_plugin_enabled(patterns, p.name) and p.is_available(ctx):
            enabled.append(p)

    started = datetime.now(UTC)
    started_ts = time.time()
    reports: list[PluginReport] = []

    if serial:
        for p in enabled:
            reports.append(_run_plugin(p, ctx, no_cache=no_cache))
    else:
        with ThreadPoolExecutor(max_workers=min(16, len(enabled) or 1)) as ex:
            futures = {ex.submit(_run_plugin, p, ctx, no_cache): p for p in enabled}
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    reports.append(fut.result(timeout=timeout))
                except TimeoutError:
                    reports.append(
                        PluginReport(
                            name=p.name,
                            candidates_found=0,
                            candidates=[],
                            error=f"timed out after {timeout}s",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    reports.append(
                        PluginReport(
                            name=p.name,
                            candidates_found=0,
                            candidates=[],
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )

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
