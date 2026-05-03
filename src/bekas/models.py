"""Core models for bekas."""

from __future__ import annotations

import enum
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Confidence(enum.StrEnum):
    """Safety confidence tier for a candidate.

    Attributes:
        SAFE: Safe to remove automatically.
        REVIEW: Review before removal.
        MANUAL: Manual intervention required.
    """

    SAFE = "safe"
    REVIEW = "review"
    MANUAL = "manual"


class Candidate(BaseModel):
    """A single candidate for cleanup discovered by a plugin.

    Attributes:
        id: Unique identifier for the candidate.
        category: Plugin category (e.g., "docker.image").
        size_bytes: Estimated size in bytes.
        path_or_handle: Filesystem path or opaque handle.
        confidence: Safety tier.
        reason: Human-readable explanation.
        mtime: Optional last-modification timestamp (Unix epoch seconds).
            Used for age-based sorting and freshness checks.
        metadata: Additional key-value data.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    size_bytes: int
    path_or_handle: str
    confidence: Confidence
    reason: str
    mtime: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """A subset of candidates approved for removal.

    Attributes:
        audit_id: Identifier of the originating audit.
        created_at: When the plan was created.
        candidates: Candidates included in this plan.
        fingerprints: Per-candidate freshness fingerprints keyed by candidate ID.
            Each fingerprint is a dict of ``{"exists": bool, "size_bytes": int,
            "mtime": int | None}`` for path-based candidates.
        signed_at: Timestamp when the plan was cryptographically signed.
            Used to warn on stale plans.
    """

    audit_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    candidates: list[Candidate] = Field(default_factory=list)
    fingerprints: dict[str, dict[str, Any]] = Field(default_factory=dict)
    signed_at: datetime | None = None

    def total_bytes(self) -> int:
        """Return the total size of all candidates in bytes.

        Returns:
            Sum of size_bytes across all candidates.
        """
        return sum(c.size_bytes for c in self.candidates)

    def by_category(self) -> dict[str, list[Candidate]]:
        """Group candidates by their top-level category.

        Returns:
            Mapping from category prefix to list of candidates.
        """
        groups: dict[str, list[Candidate]] = {}
        for c in self.candidates:
            groups.setdefault(c.category, []).append(c)
        return groups

    def by_confidence(self) -> dict[Confidence, list[Candidate]]:
        """Group candidates by confidence tier.

        Returns:
            Mapping from Confidence to list of candidates.
        """
        groups: dict[Confidence, list[Candidate]] = {}
        for c in self.candidates:
            groups.setdefault(c.confidence, []).append(c)
        return groups


class RemovalResult(BaseModel):
    """Result of removing a single candidate.

    Attributes:
        success: Whether the operation succeeded.
        bytes_freed: Bytes reclaimed.
        undo_token: Optional token to undo the operation.
        log: Human-readable log message.
    """

    success: bool
    bytes_freed: int
    undo_token: str | None = None
    log: str = ""


class RunResult(BaseModel):
    """Result of applying a plan (one run).

    Attributes:
        run_id: Unique run identifier.
        timestamp: When the run completed.
        audit_id: Identifier of the originating audit.
        per_candidate: List of (candidate, result) tuples.
        total_bytes_freed: Total bytes reclaimed across all candidates.
    """

    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit_id: str
    per_candidate: list[tuple[Candidate, RemovalResult]] = Field(default_factory=list)
    total_bytes_freed: int = 0


class SystemInfo(BaseModel):
    """System information for audit output.

    Attributes:
        os: Operating system name.
        arch: Machine architecture.
        free_disk_bytes: Available disk space in bytes.
    """

    os: str
    arch: str
    free_disk_bytes: int


class AuditReport(BaseModel):
    """Full audit report output.

    Attributes:
        audit_id: Unique audit identifier.
        started_at: When the audit started.
        duration_ms: Duration in milliseconds.
        system: System information.
        plugins: Per-plugin reports.
        summary: Aggregated summary.
    """

    audit_id: str
    started_at: datetime
    duration_ms: int
    system: SystemInfo
    plugins: list[PluginReport] = Field(default_factory=list)
    summary: AuditSummary


class PluginReport(BaseModel):
    """Per-plugin audit results.

    Attributes:
        name: Plugin name.
        candidates_found: Number of candidates discovered (pre-filter).
        candidates: Candidates after safety filtering.
    """

    name: str
    candidates_found: int
    candidates: list[Candidate] = Field(default_factory=list)


class AuditSummary(BaseModel):
    """Aggregated audit summary.

    Attributes:
        total_candidates: Total actionable candidates.
        total_bytes: Total reclaimable bytes.
        by_confidence: Breakdown by confidence tier.
    """

    total_candidates: int = 0
    total_bytes: int = 0
    by_confidence: dict[str, dict[str, int]] = Field(default_factory=dict)


@dataclass
class Context:
    """Execution context passed to plugins.

    Attributes:
        dry_run: If True, do not perform destructive operations.
        config: Active profile configuration dictionary.
        verbose: Whether to enable verbose output.
    """

    dry_run: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    verbose: bool = False

    def run_command(
        self,
        cmd: list[str],
        cwd: Path | str | None = None,
        timeout: int | None = 30,
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess command safely.

        Args:
            cmd: Command and arguments to execute.
            cwd: Working directory for the command. Defaults to None.
            timeout: Timeout in seconds. Defaults to 30.

        Returns:
            CompletedProcess with stdout, stderr, and returncode.
        """
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def which(self, name: str) -> str | None:
        """Locate a command on the system PATH.

        Args:
            name: Command name to search for.

        Returns:
            Full path to the command, or None if not found.
        """
        return shutil.which(name)
