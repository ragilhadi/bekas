"""Plugin base class and discovery."""

from __future__ import annotations

import importlib.metadata as metadata
import platform
from abc import ABC, abstractmethod
from collections.abc import Iterator

from bekas.models import Candidate, Context, RemovalResult


class Plugin(ABC):
    """Base class for all bekas plugins."""

    name: str = ""
    description: str = ""
    requires_commands: list[str] = []
    supported_platforms: list[str] | None = None

    def is_available(self, ctx: Context) -> bool:
        """Check if this plugin can run on this system."""
        if self.supported_platforms and platform.system().lower() not in self.supported_platforms:
            return False
        for cmd in self.requires_commands:
            if ctx.which(cmd) is None:
                return False
        return True

    @abstractmethod
    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield candidates. Pure read operation."""
        yield from []

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Remove a candidate. Override if plugin supports deletion."""
        return RemovalResult(success=False, bytes_freed=0, log="Plugin does not support removal.")

    def supports_quarantine(self) -> bool:
        return False

    def supports_undo(self) -> bool:
        return False

    def quarantine(self, candidate: Candidate, ctx: Context, quarantine_dir: str, run_id: str | None = None) -> RemovalResult:
        """Quarantine a candidate instead of deleting."""
        return RemovalResult(success=False, bytes_freed=0, log="Plugin does not support quarantine.")

    def undo(self, candidate: Candidate, ctx: Context, undo_token: str) -> RemovalResult:
        """Undo a previous removal."""
        return RemovalResult(success=False, bytes_freed=0, log="Plugin does not support undo.")


def discover_plugins() -> list[Plugin]:
    """Discover plugins via entry points."""
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
