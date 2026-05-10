"""Plugin base class and discovery."""

from __future__ import annotations

import importlib.metadata as metadata
import platform
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

from bekas.models import Candidate, Context, RemovalResult


@dataclass(frozen=True)
class Capabilities:
    """Manifest of plugin capabilities for scheduling and compatibility checks.

    Attributes:
        quarantine: Whether the plugin supports quarantine operations.
        parallel_safe: Whether the plugin can safely run in parallel with others.
        requires_network: Whether the plugin needs network access.
        requires_root: Whether the plugin needs root/admin privileges.
        requires_cli: Tuple of external CLI commands required.
        platforms: Supported platform identifiers.
        estimated_runtime: Expected runtime tier (fast / medium / slow).
    """

    quarantine: bool = False
    parallel_safe: bool = True
    requires_network: bool = False
    requires_root: bool = False
    requires_cli: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ("linux", "darwin", "windows")
    estimated_runtime: str = "fast"

    def __post_init__(self) -> None:
        if self.estimated_runtime not in ("fast", "medium", "slow"):
            raise ValueError("estimated_runtime must be 'fast', 'medium', or 'slow'")


class Plugin(ABC):
    """Base class for all bekas plugins.

    Subclasses must define ``name``, ``description``, and implement
    ``discover``. Optional lifecycle hooks (remove, quarantine, undo)
    may be overridden if the plugin supports them.

    Attributes:
        name: Plugin identifier used for filtering and categorization.
        description: Human-readable description.
        requires_commands: List of external commands required for this plugin.
        supported_platforms: Optional list of platform names
            (e.g., ``["darwin", "linux", "windows"]``).
        capabilities: Capability manifest for the runner.
    """

    name: str = ""
    description: str = ""
    requires_commands: list[str] = []
    supported_platforms: list[str] | None = None
    capabilities: Capabilities = Capabilities()

    def is_available(self, ctx: Context) -> bool:
        """Check if this plugin can run on the current system.

        Validates platform support and required command availability.

        Args:
            ctx: Execution context providing ``which``.

        Returns:
            True if the plugin is usable, False otherwise.
        """
        if self.supported_platforms and platform.system().lower() not in self.supported_platforms:
            return False
        # Also validate capabilities.platforms if present
        if self.capabilities.platforms and platform.system().lower() not in self.capabilities.platforms:
            return False
        for cmd in self.requires_commands:
            if ctx.which(cmd) is None:
                return False
        for cmd in self.capabilities.requires_cli:
            if ctx.which(cmd) is None:
                return False
        return True

    @abstractmethod
    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield cleanup candidates.

        This is a pure read operation and must not modify the system.

        Args:
            ctx: Execution context with config and utility methods.

        Yields:
            Candidate objects representing potential cleanup targets.
        """
        yield from []

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Remove a candidate permanently.

        Override if the plugin supports deletion.

        Args:
            candidate: Candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the removal attempt.
        """
        return RemovalResult(success=False, bytes_freed=0, log="Plugin does not support removal.")

    def supports_quarantine(self) -> bool:
        """Return True if the plugin supports quarantine operations.

        Returns:
            Whether quarantine is supported.
        """
        return self.capabilities.quarantine

    def supports_undo(self) -> bool:
        """Return True if the plugin supports undo operations.

        Returns:
            Whether undo is supported.
        """
        return False

    def quarantine(
        self,
        candidate: Candidate,
        ctx: Context,
        quarantine_dir: str,
        run_id: str | None = None,
    ) -> RemovalResult:
        """Quarantine a candidate instead of deleting.

        Override if the plugin supports quarantine.

        Args:
            candidate: Candidate to quarantine.
            ctx: Execution context.
            quarantine_dir: Directory where quarantined items are stored.
            run_id: Optional run identifier for tracking.

        Returns:
            Result of the quarantine attempt.
        """
        return RemovalResult(success=False, bytes_freed=0, log="Plugin does not support quarantine.")

    def undo(self, candidate: Candidate, ctx: Context, undo_token: str) -> RemovalResult:
        """Undo a previous removal using the provided token.

        Override if the plugin supports undo.

        Args:
            candidate: Candidate that was previously removed.
            ctx: Execution context.
            undo_token: Token returned by a prior removal or quarantine.

        Returns:
            Result of the undo attempt.
        """
        return RemovalResult(success=False, bytes_freed=0, log="Plugin does not support undo.")


def discover_plugins() -> list[Plugin]:
    """Discover plugins via entry points.

    Scans the ``bekas.plugins`` entry point group and instantiates
    each discovered class if it is a ``Plugin`` subclass.

    Returns:
        List of instantiated plugin objects.
    """
    plugins: list[Plugin] = []
    for ep in metadata.entry_points(group="bekas.plugins"):
        try:
            cls = ep.load()
            inst = cls()
            if isinstance(inst, Plugin):
                plugins.append(inst)
        except Exception:
            # Silently skip broken plugins; could log in future
            pass
    return plugins
