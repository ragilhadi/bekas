# Safety Hardening in bekas

This document describes the safety mechanisms that prevent `bekas` from
deleting system-critical data.

## Exclusion Rules

The following paths are **never** considered safe for deletion:

- **System directories:** `/`, `/usr`, `/etc`, `/bin`, `/sbin`, `/var` (except known temp subpaths), `/System` (macOS), `/Library` (macOS — except specific subpaths), `C:\Windows`, `C:\Program Files*` (Windows).
- **Home directory itself:** Only contents of subdirectories under `$HOME` are candidates; `$HOME` itself is never deleted.
- **Active virtual environments:** Anything inside `$VIRTUAL_ENV` or `$CONDA_PREFIX` if currently set.
- **Git repositories themselves:** `.git` directories are protected; only their orphan objects/branches may be cleaned by `git_branches`.
- **Non-local filesystems:** Paths on NFS, SMB, or FUSE mounts are excluded (detected via `psutil.disk_partitions`).
- **Symlinks pointing outside `$HOME`:** Followed and rejected.
- **Recently modified files:** Files modified within the last `min_quiet_hours` (default 6 hours) are excluded.

## Per-Plugin Safety Notes

### Package-manager caches (`pip.cache`, `uv.cache`, `poetry.cache`, `cargo.registry`, `go.modcache`, `gradle.caches`, `maven.repo`)
- These are **pure caches** — they can always be recreated by the respective package manager. Tier is `SAFE` when the tool is detected on `PATH`; otherwise `REVIEW`.
- `maven.repo` keeps the **latest 2 versions** of each artifact and only marks older ones as candidates.
- `pnpm.store` and `yarn.cache` are **content-addressable**; removing the global store breaks linked projects until the next install. Tier is always `REVIEW`.

### Browser caches (`browser.chrome`, `browser.firefox`, `browser.safari`)
- Only cache subdirectories (`Cache/`, `cache2/`, `Code Cache/`, `GPUCache/`, `Service Worker/CacheStorage/`) are touched.
- Profile data (bookmarks, history, passwords, cookies) is **never** targeted.
- Tier is `REVIEW` because users notice when their browser feels "fresh".

### IDE leftovers (`vscode.extensions.orphan`, `jetbrains.caches`, `xcode.derived_data`)
- `vscode.extensions.orphan` requires an `extensions.json` file to exist; it silently yields nothing otherwise.
- `jetbrains.caches` only marks caches for **older IDE versions** than the latest installed.
- `xcode.simulators` only targets **unavailable** simulator runtimes left after Xcode upgrades.

### Trash / Recycle Bin (`system.trash`)
- Items are already in a soft-delete location. Tier is `SAFE`.
- On Linux (XDG), the matching `.trashinfo` files in `info/` are also removed.

## Safety API

`safety.is_safe_to_delete(path) -> tuple[bool, str]`

Returns `(True, "")` if the path passes all exclusion rules, or
`(False, reason)` with a human-readable explanation of why it was blocked.

## Property-Based Testing

The `tests/test_safety.py` property-based suite (using `hypothesis`) generates
arbitrary paths and verifies that **no system path ever returns `(True, _)`**.

## Confidence Tiers

Every candidate is assigned a confidence tier:

- **SAFE** — Deletion is fully reversible or recreatable (e.g., pip cache, Rust target).
- **REVIEW** — May affect active workflows; requires user review (e.g., node_modules in a recently-touched project).
- **MANUAL** — High risk; only removed with explicit opt-in (e.g., browser caches, quarantined items older than retention).

## Reporting Safety Issues

If you believe a safety rule is missing or incorrect, please open an issue
with the path pattern and expected behavior.
