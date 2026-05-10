# Safety Model

This document describes the safety mechanisms that prevent `bekas` from deleting files you care about or damaging your system.

---

## Core Principles

1. **Read-only by default** — `bekas audit` and `bekas plan` never touch the filesystem.
2. **Dry-run by default** — `bekas clean` without `--apply` only prints a preview.
3. **Graduated risk** — Every candidate is assigned a confidence tier:
   - `SAFE` — Low risk, e.g. dangling Docker image with no project nearby.
   - `REVIEW` — Medium risk, e.g. old download from an active project.
   - `MANUAL` — High risk, e.g. git branch on an active project or a container in use.
4. **Typed confirmation gate** — `clean --apply` requires typing `"yes"` (or a random 4-character token for high-risk plans) before any destructive action.
5. **Quarantine first** — Where supported, items are moved to a quarantine directory instead of being permanently deleted. You can `bekas quarantine restore` them later.
6. **Undo log** — Every `clean --apply` is recorded in an SQLite database with per-candidate results and undo tokens.
7. **Signed plans** — Saved plan files include a signature so you can detect tampering before applying them later.

---

## Hard Exclusions

The following paths and patterns are **automatically excluded** from all candidates. No plugin can yield a candidate inside these zones, regardless of its confidence tier.

### System directories

- `/`, `/usr`, `/etc`, `/bin`, `/sbin`, `/var` (except known temp subpaths), `/System` (macOS), `/Library` (macOS — except specific plugin-allowed subpaths).
- `C:\Windows`, `C:\Program Files*`, `C:\ProgramData` (Windows).
- Any path owned by `root` when bekas is not running as root.

### Home directory boundaries

- `$HOME` itself is excluded — only **contents** of subdirectories (e.g. `~/Downloads`, `~/.cache`) are eligible.
- `.git` directories themselves are excluded. Only orphan objects or merged branches are considered, and even then only via the `git.branches` plugin which operates through `git` commands, not direct filesystem deletion.

### Virtual environments

- Any path inside the currently active `$VIRTUAL_ENV` or `$CONDA_PREFIX` is excluded.
- This prevents accidentally deleting the interpreter or packages you are actively using.

### Non-local filesystems

- Paths on NFS, SMB, FUSE, or other network/virtual filesystems are excluded.
- Detected via `psutil.disk_partitions()`; any mount whose `fstype` is not in the local whitelist (`ext*`, `xfs`, `btrfs`, `apfs`, `hfs`, `ntfs`, `tmpfs`) is skipped.

### Symlinks outside `$HOME`

- Symlinks whose resolved target lies outside `$HOME` are excluded.
- This prevents following a symlink like `~/old-data -> /var/backups` and deleting system backups.

### Recently modified files

- Files modified within the last `min_quiet_hours` (default 6 hours) are excluded.
- This is a heuristic to avoid deleting files you are still actively working with.

---

## Confidence Tier Assignment

| Scenario | Typical Tier | Rationale |
|---|---|---|
| Dangling Docker image (no tags, no containers) | `SAFE` | Fully reproducible; `docker pull` restores it. |
| Stopped Docker container (no auto-restart) | `REVIEW` | May contain data volumes you forgot to back up. |
| Running Docker container | `MANUAL` | Explicitly in use; delete only after stopping. |
| Old download (> 180 days, no project nearby) | `SAFE` | Low value, easily re-downloaded. |
| Old download from an active project directory | `REVIEW` | You might still reference it in a script or README. |
| Orphaned Python venv (no parent project) | `SAFE` | `pip install` recreates it. |
| Python venv inside an active project | `MANUAL` | Required for development; do not auto-delete. |
| Merged git branch (> 90 days old) | `SAFE` | Already merged; `git reflog` can recover if needed. |
| Unmerged git branch on active project | `MANUAL` | May be a feature branch you intend to merge. |
| Old `node_modules` (no `package.json` nearby) | `SAFE` | `npm install` recreates it. |
| `node_modules` in a project with recent commits | `MANUAL` | Needed for development; rebuild is slow. |
| macOS Trash / Linux Trash contents | `SAFE` | Already a soft-delete location. |
| Xcode DerivedData (old project, no recent build) | `SAFE` | Rebuilds automatically. |
| Xcode DerivedData (active project) | `REVIEW` | Rebuild is fast, but may interrupt current work. |

---

## Quarantine Behavior

When `quarantine_enabled: true` (default) in your profile:

1. Files from plugins that support quarantine are **moved** (not copied) to `~/.local/share/bekas/quarantine/<run_id>/`.
2. The original path, size, metadata, and category are recorded in the quarantine registry.
3. You can restore with `bekas quarantine restore <quarantine_id>`.
4. Old quarantine items are automatically purged after `quarantine_retention_days` (default 30).

When quarantine is **disabled** or a plugin does **not** support quarantine, deletion is **permanent**. High-risk plans (anything `MANUAL`-tier or hard-delete on > 5 GB) require a random 4-character token.

---

## Plan Re-validation

When applying a saved plan file (`--plan-file`):

1. Each candidate is re-stated. If the path no longer exists, it is skipped silently.
2. If the file grew or has a newer mtime than recorded in the plan, it is skipped unless `--force-stale` is passed.
3. Plans older than 7 days trigger a warning but still apply if they pass re-validation.

This protects against applying a plan to a system that has changed since the plan was created.

---

## Reporting Safety Issues

If you believe bekas deleted something it should not have, or if you find a way to bypass the exclusion engine:

1. Check the undo log: `bekas history` and `bekas undo`.
2. Check the quarantine: `bekas quarantine list` and `bekas quarantine restore`.
3. Open an issue on GitHub with the candidate ID, the command you ran, and your operating system.
