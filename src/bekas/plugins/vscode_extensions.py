"""VS Code orphan extensions plugin for bekas.

Discovers VS Code extensions in ``~/.vscode/extensions`` that are not
present in the current ``extensions.json``.  Tier ``REVIEW``.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class VscodeExtensionsPlugin(Plugin):
    """Finds VS Code extensions not listed in extensions.json.

    Scans ``~/.vscode/extensions``.  Extensions not present in the user's
    ``extensions.json`` are considered orphans.  Tier ``REVIEW`` because
    the user may still want some extensions even if not in the current
    exported list.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: ``code`` checked at runtime for availability.
    """

    name = "vscode.extensions.orphan"
    description = "Finds VS Code extensions not in extensions.json."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="fast")

    def is_available(self, ctx: Context) -> bool:
        """Check if VS Code extensions directory exists.

        Args:
            ctx: Execution context.

        Returns:
            True if the VS Code extensions directory exists.
        """
        return _extensions_dir().exists()

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield VS Code orphan extension candidates.

        Requires a ``~/.vscode/extensions.json`` file to determine which
        extensions are "known".  If the file does not exist the plugin
        silently yields nothing so it does not flag every installed
        extension as an orphan.

        Args:
            ctx: Execution context with plugin_settings.

        Yields:
            Candidate objects representing orphan VS Code extensions.
        """
        ext_dir = _extensions_dir()
        if not ext_dir.exists():
            return

        known = _known_extensions()
        if not known:
            # No extensions.json means we can't determine orphans safely.
            return

        for entry in ext_dir.iterdir():
            if not entry.is_dir():
                continue
            ext_id = entry.name
            # Skip if it's in the known list
            if ext_id in known:
                continue

            size = _du(entry)
            yield Candidate(
                id=f"vscode:{ext_id}",
                category="vscode.extensions.orphan",
                size_bytes=size,
                path_or_handle=str(entry),
                confidence=Confidence.REVIEW,
                reason="VS Code extension not present in current extensions.json.",
                metadata={"extension_id": ext_id, "path": str(entry)},
            )

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete a VS Code extension directory.

        Args:
            candidate: Extension candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the deletion attempt.
        """
        path = Path(candidate.path_or_handle)
        if not path.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size = _du(path)
            shutil.rmtree(path)
            return RemovalResult(success=True, bytes_freed=size, log="Deleted")
        except Exception as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return False because extensions cannot be undone.

        Returns:
            False.
        """
        return False


def _extensions_dir() -> Path:
    """Return the VS Code extensions directory path.

    Returns:
        Path to ``~/.vscode/extensions``.
    """
    return Path.home() / ".vscode" / "extensions"


def _known_extensions() -> set[str]:
    """Read the user's extensions.json and return a set of extension IDs.

    If the file does not exist or is malformed, returns an empty set.

    Returns:
        Set of known extension identifiers (publisher.name format).
    """
    ext_json = _extensions_dir().parent / "extensions.json"
    if not ext_json.exists():
        return set()
    try:
        data = json.loads(ext_json.read_text())
        if isinstance(data, list):
            return {item for item in data if isinstance(item, str)}
        if isinstance(data, dict):
            # Some users store as {"recommendations": [...]}
            recs = data.get("recommendations", [])
            if isinstance(recs, list):
                return {item for item in recs if isinstance(item, str)}
    except Exception:
        pass
    return set()


def _du(path: Path) -> int:
    """Compute total byte size of a path recursively.

    Args:
        path: File or directory to measure.

    Returns:
        Total size in bytes.
    """
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return total
