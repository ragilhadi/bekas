"""Audit runner orchestrates plugins to collect candidates."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from bekas.config import is_plugin_enabled, load_config, profile_for
from bekas.database import _init_db, get_audit_cache, prune_audit_cache, set_audit_cache
from bekas.fsmeasure import measure_path
from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, Context, PluginReport, SystemInfo
from bekas.plugin import Plugin
from bekas.safety import filter_candidates


def _fingerprint(path: str) -> str:
    """Compute a cheap content-aware fingerprint for caching.

    Files hash their size and mtime. Directories hash the total size and the
    newest mtime found anywhere in the tree (bounded walk), because a
    directory's own mtime does not change when nested files grow.

    Args:
        path: Filesystem path to fingerprint.

    Returns:
        Hash string, or empty string if the path cannot be inspected.
    """
    try:
        size, mtime = measure_path(Path(path))
    except (OSError, ValueError):
        return ""
    data = json.dumps({"size": size, "mtime": mtime}, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


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

    A plugin that raises does not abort the audit; the exception is recorded
    in ``PluginReport.error`` so the failure is visible instead of looking
    like "nothing found".

    Args:
        plugin: Plugin instance to run.
        ctx: Execution context passed to the plugin.
        no_cache: If True, skip audit cache reads/writes.

    Returns:
        PluginReport containing discovered candidates (empty on failure) and
        the error message, if any.
    """
    try:
        candidates = _cached_discover(plugin, ctx, no_cache=no_cache)
    except Exception as exc:
        return PluginReport(
            name=plugin.name,
            candidates_found=0,
            candidates=[],
            error=f"{type(exc).__name__}: {exc}",
        )
    return PluginReport(name=plugin.name, candidates_found=len(candidates), candidates=candidates)


class _Job:
    """One plugin run executing on its own daemon thread."""

    def __init__(self, plugin: Plugin) -> None:
        self.plugin = plugin
        self.done = threading.Event()
        self.started_at: float | None = None
        self.report: PluginReport | None = None
        self.slot_released = False


def _run_jobs(
    plugins: list[Plugin],
    ctx: Context,
    no_cache: bool,
    timeout: float,
    max_parallel: int,
) -> list[PluginReport]:
    """Run plugins concurrently, enforcing a wall-clock timeout per plugin.

    Each plugin runs on a daemon thread so that one stuck in a blocking call
    (e.g. a hung ``docker`` daemon) can be abandoned: Python threads cannot be
    killed, but being daemons they never keep the process alive. The timeout
    counts from the moment the plugin actually starts, not from when it queued.

    Args:
        plugins: Plugins to run.
        ctx: Execution context.
        no_cache: Bypass the audit cache.
        timeout: Seconds a plugin may run before it is reported as timed out.
        max_parallel: Maximum plugins running at once.

    Returns:
        One report per plugin, in the order given.
    """
    slots = threading.Semaphore(max(1, max_parallel))
    slot_lock = threading.Lock()
    jobs = [_Job(p) for p in plugins]

    def _release_slot(job: _Job) -> None:
        # A timed-out plugin hands its slot back at once so it cannot starve the
        # plugins queued behind it; the flag stops the thread releasing it twice
        # if it ever finishes.
        with slot_lock:
            if not job.slot_released:
                job.slot_released = True
                slots.release()

    def _work(job: _Job) -> None:
        slots.acquire()
        try:
            job.started_at = time.monotonic()
            job.report = _run_plugin(job.plugin, ctx, no_cache=no_cache)
        except BaseException as exc:  # noqa: BLE001
            job.report = PluginReport(
                name=job.plugin.name, candidates_found=0, candidates=[], error=f"{type(exc).__name__}: {exc}"
            )
        finally:
            _release_slot(job)
            job.done.set()

    for job in jobs:
        threading.Thread(target=_work, args=(job,), name=f"bekas-{job.plugin.name}", daemon=True).start()

    reports: list[PluginReport] = []
    for job in jobs:
        while not job.done.wait(0.05):
            if job.started_at is not None and time.monotonic() - job.started_at > timeout:
                break
        if job.done.is_set() and job.report is not None:
            reports.append(job.report)
        else:
            _release_slot(job)
            reports.append(
                PluginReport(
                    name=job.plugin.name,
                    candidates_found=0,
                    candidates=[],
                    error=f"timed out after {timeout:g}s",
                )
            )
    return reports


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
    reports = _run_jobs(enabled, ctx, no_cache, timeout, max_parallel=1 if serial else 16)

    # Filter excluded candidates
    for r in reports:
        r.candidates = filter_candidates(r.candidates, user_exclusions, profile.min_quiet_hours)

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
