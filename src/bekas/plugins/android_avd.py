"""Android AVD plugin for bekas.

Discovers Android Virtual Device images under ``~/.android/avd`` (each AVD is
an ``<name>.avd`` directory plus an ``<name>.ini`` sidecar) and old Flutter
SDK directories (``~/flutter`` or ``~/development`` only when multiple SDK
version directories exist — the newest one is treated as current and never
flagged; a single install is left alone).

Everything here may hold user data (emulator installs, app snapshots, SDK
caches that cost real bandwidth to rebuild), so all candidates are ``REVIEW``.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

from bekas.fsmeasure import measure_path
from bekas.models import Candidate, Confidence, Context, RemovalResult
from bekas.plugin import Capabilities, Plugin


class AndroidAvdPlugin(Plugin):
    """Finds Android AVD images and old Flutter SDK directories.

    Attributes:
        name: Plugin identifier.
        description: Human-readable description.
        requires_commands: No external commands required.
    """

    name = "android.avd"
    categories = ("android.avd", "flutter.sdk")
    description = "Finds Android AVD images and old Flutter SDK versions."
    requires_commands = []
    capabilities = Capabilities(quarantine=False, estimated_runtime="medium")

    def is_available(self, ctx: Context) -> bool:
        """Check whether any AVD or old Flutter SDK exists under home.

        Args:
            ctx: Execution context.

        Returns:
            True if ~/.android/avd contains AVDs or an old Flutter SDK exists.
        """
        return next(self.discover(ctx), None) is not None

    def discover(self, ctx: Context) -> Iterator[Candidate]:
        """Yield AVD and old-Flutter-SDK candidates.

        Args:
            ctx: Execution context.

        Yields:
            Candidate objects for AVD images and non-current Flutter SDKs.
        """
        home = Path.home()
        yield from self._discover_avds(home / ".android" / "avd")
        flutter_settings = ctx.config.get("plugin_settings", {}).get("android.avd", {})
        current_markers = flutter_settings.get("current_symlink_markers", ["current"])
        yield from self._discover_flutter_sdks(home, current_markers)

    def remove(self, candidate: Candidate, ctx: Context) -> RemovalResult:
        """Delete an AVD or an old Flutter SDK directory.

        Only the exact ``path_or_handle`` is deleted, plus the matching
        ``.ini`` sidecar recorded in metadata for AVDs; nothing else in the
        parent is touched.

        Args:
            candidate: Candidate to remove.
            ctx: Execution context.

        Returns:
            Result of the removal attempt.
        """
        if ctx.dry_run:
            return RemovalResult(success=True, bytes_freed=0, undo_token=None, log="dry-run")
        target = Path(candidate.path_or_handle)
        if not target.exists():
            return RemovalResult(success=False, bytes_freed=0, log="Path does not exist")
        try:
            size_bytes, _ = measure_path(target)
            shutil.rmtree(target)
            sidecar_str = candidate.metadata.get("sidecar", "")
            sidecar = Path(sidecar_str) if sidecar_str else None
            if sidecar is not None and sidecar.is_file() and sidecar.parent == target.parent:
                sidecar.unlink()
            return RemovalResult(success=True, bytes_freed=size_bytes, undo_token=None, log="Deleted")
        except OSError as exc:
            return RemovalResult(success=False, bytes_freed=0, log=str(exc))

    def supports_undo(self) -> bool:
        """Return False because AVD and SDK removal cannot be undone.

        Returns:
            Whether undo is supported.
        """
        return False

    def _discover_avds(self, avd_root: Path) -> Iterator[Candidate]:
        """Yield AVD candidates from an ``.android/avd`` root.

        Args:
            avd_root: The ``~/.android/avd`` directory.

        Yields:
            Candidate objects, one per ``.avd`` image directory.
        """
        if not avd_root.is_dir():
            return
        for entry in sorted(avd_root.iterdir()):
            if not entry.is_dir() or not entry.name.endswith(".avd"):
                continue
            try:
                size_bytes, mtime = measure_path(entry)
            except OSError:
                size_bytes, mtime = 0, None
            ini = entry.with_suffix(".ini")
            has_ini = ini.is_file()
            reason = f"Android Virtual Device image '{entry.name}'."
            if has_ini:
                reason += " Its matching .ini sidecar is deleted together with the image."
            yield Candidate(
                id=f"android-avd:{entry.name}",
                category="android.avd",
                size_bytes=size_bytes,
                path_or_handle=str(entry),
                confidence=Confidence.REVIEW,
                reason=reason,
                mtime=mtime,
                metadata={
                    "path": str(entry),
                    "sidecar": str(ini) if has_ini else "",
                    "name": entry.name[: -len(".avd")],
                },
            )

    def _discover_flutter_sdks(self, home: Path, current_markers: list[str]) -> Iterator[Candidate]:
        """Yield candidates for non-current Flutter SDK versions.

        Only flags SDK roots when multiple version directories exist; the
        newest one (highest parsed version) is treated as current and
        skipped. Roots that hold a "current"-style marker directory are the
        active install and never flagged.

        Args:
            home: The user home directory.

        Yields:
            Candidate objects for old Flutter SDK directories.
        """
        for sdk_root in (home / "flutter", home / "development"):
            if not sdk_root.is_dir():
                continue
            version_dirs = _flutter_version_dirs(sdk_root, current_markers)
            if len(version_dirs) < 2:
                continue  # a single SDK install is likely the active one
            current = version_dirs[-1]
            for vdir in version_dirs[:-1]:
                try:
                    size_bytes, mtime = measure_path(vdir)
                except OSError:
                    size_bytes, mtime = 0, None
                yield Candidate(
                    id=f"flutter-sdk:{vdir}",
                    category="flutter.sdk",
                    size_bytes=size_bytes,
                    path_or_handle=str(vdir),
                    confidence=Confidence.REVIEW,
                    reason=f"Old Flutter SDK '{vdir.name}'; newest installed SDK is '{current.name}'.",
                    mtime=mtime,
                    metadata={"path": str(vdir), "sdk_root": str(sdk_root), "name": vdir.name},
                )


def _flutter_version_dirs(sdk_root: Path, current_markers: list[str]) -> list[Path]:
    """Find Flutter SDK version directories inside an SDK root.

    Recognizes versioned subdirectories such as ``3.10.0`` or
    ``flutter-3.7.1`` directly under the SDK root, plus version dirs inside
    ``bin/cache`` for single-install layouts. A root containing a
    "current"-style marker directory is skipped entirely.

    Args:
        sdk_root: Root to search (``~/flutter`` or ``~/development``).
        current_markers: Lowercased directory names treated as "current".

    Returns:
        Version directories sorted oldest-first (newest last).
    """
    markers = {m.lower() for m in current_markers}
    found: list[tuple[tuple[int, ...] | tuple[int, int], str, Path]] = []
    candidates: list[tuple[str, Path]] = [(d.name, d) for d in sdk_root.iterdir() if d.is_dir()]
    cache_dir = sdk_root / "bin" / "cache"
    if cache_dir.is_dir():
        candidates += [(d.name, d) for d in cache_dir.iterdir() if d.is_dir() and _VERSION_PREFIX.search(d.name)]
    if any(name.lower() in markers for name, _ in candidates):
        return []  # marker dir present: this SDK root is the active install
    for name, vdir in candidates:
        if name.startswith("."):
            continue
        version = _parse_version(name)
        if version is None or not (vdir / "bin" / "flutter").exists():
            continue
        if vdir.parent.name == "cache":
            version = (*version, 0)  # cache-managed dirs sort behind bare installs
        found.append((version, name, vdir))
    return [v for _, _, v in sorted(found, key=lambda t: (t[0], t[1]))]


_VERSION_PREFIX = re.compile(r"(?:^|[_\-])(\d[\d_]*(?:\.\d[\d_]*)*)")


def _parse_version(name: str) -> tuple[int, ...] | None:
    """Parse a leading version out of a directory name.

    Args:
        name: Directory name, e.g. ``3.10.0`` or ``flutter-2.10``.

    Returns:
        Tuple of ints for sorting, or None when no version is present.
    """
    m = _VERSION_PREFIX.search(name)
    if not m:
        return None
    return tuple(int(part) for part in m.group(1).split("."))
