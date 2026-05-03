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
    """Safety confidence tier for a candidate."""

    SAFE = "safe"
    REVIEW = "review"
    MANUAL = "manual"


class Candidate(BaseModel):
    """A single candidate for cleanup discovered by a plugin."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    size_bytes: int
    path_or_handle: str
    confidence: Confidence
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """A subset of candidates approved for removal."""

    audit_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    candidates: list[Candidate] = Field(default_factory=list)

    def total_bytes(self) -> int:
        return sum(c.size_bytes for c in self.candidates)

    def by_category(self) -> dict[str, list[Candidate]]:
        groups: dict[str, list[Candidate]] = {}
        for c in self.candidates:
            groups.setdefault(c.category, []).append(c)
        return groups

    def by_confidence(self) -> dict[Confidence, list[Candidate]]:
        groups: dict[Confidence, list[Candidate]] = {}
        for c in self.candidates:
            groups.setdefault(c.confidence, []).append(c)
        return groups


class RemovalResult(BaseModel):
    """Result of removing a single candidate."""

    success: bool
    bytes_freed: int
    undo_token: str | None = None
    log: str = ""


class RunResult(BaseModel):
    """Result of applying a plan (one run)."""

    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit_id: str
    per_candidate: list[tuple[Candidate, RemovalResult]] = Field(default_factory=list)
    total_bytes_freed: int = 0


class SystemInfo(BaseModel):
    """System information for audit output."""

    os: str
    arch: str
    free_disk_bytes: int


class AuditReport(BaseModel):
    """Full audit report output."""

    audit_id: str
    started_at: datetime
    duration_ms: int
    system: SystemInfo
    plugins: list[PluginReport] = Field(default_factory=list)
    summary: AuditSummary


class PluginReport(BaseModel):
    """Per-plugin audit results."""

    name: str
    candidates_found: int
    candidates: list[Candidate] = Field(default_factory=list)


class AuditSummary(BaseModel):
    """Aggregated audit summary."""

    total_candidates: int = 0
    total_bytes: int = 0
    by_confidence: dict[str, dict[str, int]] = Field(default_factory=dict)


@dataclass
class Context:
    """Execution context passed to plugins."""

    dry_run: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    verbose: bool = False

    def run_command(
        self,
        cmd: list[str],
        cwd: Path | str | None = None,
        timeout: int | None = 30,
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess command safely."""
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def which(self, name: str) -> str | None:
        return shutil.which(name)
