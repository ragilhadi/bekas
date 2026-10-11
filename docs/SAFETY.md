# Safety Hardening in bekas

This document describes the safety mechanisms that prevent `bekas` from
deleting system-critical data.

## Exclusion Rules

The following paths are **never** considered safe for deletion:

- **System directories:** `/usr`, `/etc`, `/bin`, `/sbin`, `/lib*`, `/var/{db,lib,log,run,spool}`, `/System` (macOS), `/Library` (macOS), `C:\Windows`, `C:\Program Files*` (Windows) and everything inside them.
- **Protected locations (the directory itself, not its contents):** `/`, `/home`, `/Users`, `/root`, `/opt`, `/srv`, `/mnt`, `/media`, `/tmp`, `/var/tmp` and `$HOME`. Their children, such as `~/.cache/pip` or `/tmp/build`, can still be candidates.
- **Anything that contains a protected path:** a candidate that is a parent of `~/.ssh`, `~/.gnupg`, the bekas data directory or a system directory is excluded too, because removing it would remove them (this is why `$HOME`, `/var` and `/` can never be candidates).
- **Active virtual environments:** Anything inside `$VIRTUAL_ENV` or `$CONDA_PREFIX` if currently set.
- **Git repositories themselves:** `.git` directories are protected; only their orphan objects/branches may be cleaned by `git_branches`.
- **Non-local filesystems:** Paths on NFS, SMB, or FUSE mounts are excluded (detected via `psutil.disk_partitions`).
- **Symlinks pointing outside `$HOME`:** Rejected. The check is made on the path as supplied, before symlinks are resolved.
- **Recently modified files:** Files modified within the last `min_quiet_hours` (profile setting, default 6 hours) are excluded, both when auditing and again when a plan is applied.
- **Your own exclusions (`exclude:` in the config):** gitignore-style patterns, re-checked when a plan is applied so an exclusion added after `plan --save` still protects the path. See below.

## Exclusion patterns

Patterns in the `exclude:` list of the config use gitignore-style matching on `/`-separated paths:

- `*` and `?` match within one path segment; `**` matches across segments; `[abc]` / `[!abc]` are character classes.
- A pattern without a `/` (`node_modules`, `*.bak`) or a relative one (`foo/*.log`) matches at any depth.
- An absolute pattern (`/data/keep`) or one starting with `~/` is anchored there. `~` is only expanded at the start.
- A pattern that matches a directory also excludes everything inside it.

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
