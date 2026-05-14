# bekas

[![CI](https://github.com/ragilhadi/bekas/actions/workflows/test.yml/badge.svg)](https://github.com/ragilhadi/bekas/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/badge/coverage-80%25-green)](https://github.com/ragilhadi/bekas)
[![PyPI](https://img.shields.io/pypi/v/bekas)](https://pypi.org/project/bekas/)
[![Python](https://img.shields.io/pypi/pyversions/bekas)](https://pypi.org/project/bekas/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

One audit. Every kind of forgotten thing. Nothing deleted you cared about.

`bekas` is a CLI utility that audits your local machine for stuff you forgot — orphaned Docker images, unused git branches, old `node_modules`, dangling Python venvs, stale downloads, screenshots from years ago — and helps you safely reclaim disk space. Every destructive action is logged to an SQLite undo log and can be quarantined before permanent deletion.

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Commands](#commands)
  - [`audit`](#bekas-audit)
  - [`plan`](#bekas-plan)
  - [`clean`](#bekas-clean)
  - [`inspect`](#bekas-inspect)
  - [`history` / `undo`](#bekas-history--undo)
  - [`quarantine`](#bekas-quarantine)
  - [`plugins`](#bekas-plugins)
  - [`config`](#bekas-config)
  - [`doctor`](#bekas-doctor)
  - [`tui`](#bekas-tui)
- [Plugins](#plugins)
- [Configuration](#configuration)
- [Safety Model](#safety-model)
- [Comparison](#comparison)
- [Architecture & File Structure](#architecture--file-structure)
- [License](#license)

---

## Installation

**Recommended — use `pipx` or `uv` so bekas stays isolated from your project dependencies:**

```bash
# pipx (most systems)
pipx install bekas

# uv (fastest)
uv tool install bekas

# Or plain pip if you prefer
pip install bekas
```

Requires **Python 3.11+**. Some plugins (e.g. Docker, Git) require the corresponding CLI tools to be installed.

---

## Quick Start

```bash
# 1. Run a read-only audit
bekas audit

# 2. Preview what a clean would remove
bekas plan

# 3. Clean only the safest items (dry-run by default)
bekas clean --apply --safe-only
```

---

## Commands

### `bekas audit`

Runs a read-only scan across all available plugins and prints candidates.

```bash
bekas audit                    # Human-readable output
bekas audit --json             # JSON output
bekas audit --plugin docker    # Only run Docker plugins
bekas audit --serial           # Run plugins one at a time
bekas audit --no-cache         # Skip the audit cache (full rescan)
bekas audit --rebuild-cache    # Clear and rebuild the audit cache
bekas audit --sort-by size     # Sort by reclaimable size
bekas audit --top 20           # Only show top 20 candidates
```

### `bekas plan`

Converts the latest audit into a concrete removal plan. You can save a cryptographically signed plan to disk and apply it later.

```bash
bekas plan                     # Preview in human-readable format
bekas plan --safe-only         # Only SAFE-tier items
bekas plan --include-review    # Include REVIEW-tier items
bekas plan --save plan.json    # Save a signed plan file
```

### `bekas clean`

The only command that actually removes files. **Dry-run by default** — you must pass `--apply` to delete anything.

```bash
bekas clean --apply --safe-only              # Auto-clean safe items
bekas clean --apply --yes-all                # Skip per-category prompts
bekas clean --apply --non-interactive \
               --accept-categories "docker,tmp" # CI-friendly mode
bekas clean --apply --plan-file plan.json    # Apply a signed plan
```

Options:

- `--apply` — Actually delete (default is dry-run).
- `--safe-only` — Only include `SAFE`-tier candidates.
- `--review` / `--include-review` — Include `REVIEW`-tier candidates.
- `--yes-all` — Skip all interactive prompts.
- `--non-interactive` — Requires `--accept-categories`.
- `--accept-categories` — Comma-separated category whitelist for non-interactive runs.
- `--plan-file` — Apply a previously saved signed plan instead of re-auditing.

### `bekas inspect <id>`

Show full metadata and reasoning for a single candidate.

```bash
bekas inspect ss:Screenshot_2023-01-01.png
```

### `bekas history` / `bekas undo`

Every `clean --apply` is persisted to an SQLite undo log and a structured JSONL event log.

```bash
bekas history                  # List past runs
bekas history <run_id>         # Show details for one run
bekas history --since 30d      # Show events from the last 30 days
bekas history --since 30d --json  # Dump raw JSONL events
bekas undo                     # Undo the most recent run
bekas undo <run_id>            # Undo a specific run
```

### `bekas quarantine`

Files removed by plugins that support quarantine are moved to a quarantine directory instead of being permanently deleted. You can restore them later.

```bash
bekas quarantine list                     # Show quarantined items
bekas quarantine restore <quarantine_id>  # Restore to original path
bekas quarantine purge                    # Empty quarantine immediately
```

### `bekas plugins`

List, enable, or disable plugins.

```bash
bekas plugins list             # Show installed plugins and availability
bekas plugins enable  <name>   # Enable a plugin (edit config)
bekas plugins disable <name>   # Disable a plugin (edit config)
```

### `bekas config`

Print the current effective configuration.

```bash
bekas config
bekas config show --resolved   # Effective config after profile merge
bekas config validate            # Validate config file syntax
```

### `bekas doctor`

Diagnose the bekas runtime environment.

```bash
bekas doctor                   # Human-readable health check
bekas doctor --json            # Machine-readable output
bekas doctor --skip docker     # Skip specific checks
```

### `bekas tui`

Launch an interactive Textual TUI for browsing candidates and cleaning interactively.

```bash
bekas tui
```

---

## Plugins

| Plugin | Category | What it finds | Needs `docker` / `git` |
|---|---|---|---|
| `docker.images` | `docker.image.*` | Dangling & unused Docker images | docker |
| `docker.containers` | `docker.container.*` | Stopped containers | docker |
| `docker.buildx.cache` | `docker.buildx.cache` | Stale BuildKit cache entries | docker |
| `python.venvs` | `python.venv` | Orphaned Python virtual environments | — |
| `python.cache` | `python.cache` | `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache` | — |
| `pip.cache` | `pip.cache` | Stale pip download & wheel cache | — |
| `uv.cache` | `uv.cache` | UV package cache | — |
| `poetry.cache` | `poetry.cache` | Poetry package cache | — |
| `node.modules` | `node.modules` | Old `node_modules` directories | — |
| `pnpm.store` | `pnpm.store` | PNPM global content-addressable store | — |
| `yarn.cache` | `yarn.cache` | Yarn global cache | — |
| `rust.target` | `rust.target` | Old `target/` build directories | — |
| `cargo.registry` | `cargo.registry` | Cargo registry cache | — |
| `go.modcache` | `go.modcache` | Go module cache | — |
| `gradle.caches` | `gradle.caches` | Gradle wrapper & dependency caches | — |
| `maven.repo` | `maven.repo` | Old Maven artifact versions (keeps latest 2) | — |
| `git.branches` | `git.branch` | Fully-merged local branches | git |
| `downloads` | `downloads.file` | Old files in `~/Downloads` | — |
| `screenshots` | `screenshots.file` | Old screenshots (Desktop / Pictures) | — |
| `system.tmp` | `system.tmp` | Old temp files owned by you | — |
| `system.trash` | `system.trash` | macOS/Linux Trash contents | — |
| `dotfiles.backups` | `dotfiles.backups` | Backup files like `.zshrc.bak` | — |
| `xcode.derived_data` | `xcode.derived_data` | Stale Xcode build caches (macOS only) | — |
| `xcode.simulators` | `xcode.simulators` | Abandoned simulator runtimes (macOS only) | — |
| `browser.chrome` | `browser.chrome` | Chrome/Chromium caches (macOS/Linux/Windows) | — |
| `browser.firefox` | `browser.firefox` | Firefox caches | — |
| `browser.safari` | `browser.safari` | Safari caches (macOS only) | — |
| `vscode.extensions.orphan` | `vscode.extensions.orphan` | VS Code extensions not in extensions.json | — |
| `jetbrains.caches` | `jetbrains.caches` | Old JetBrains IDE caches | — |

Plugins are auto-discovered via `entry_points` in `pyproject.toml`. You can write your own by subclassing `bekas.plugin.Plugin`.

---

## Configuration

The first time you run `bekas`, a config file is created at the platform-specific config directory (e.g. `~/.config/bekas/config.yaml` on Linux).

Example:

```yaml
version: "1"
active_profile: default
profiles:
  default:
    enabled_plugins: ["*"]
    quarantine_enabled: true
    plugin_timeout_seconds: 60
    # Per-plugin thresholds
    plugin_settings:
      screenshots:
        min_age_days: 90
      downloads:
        min_age_days: 180
      python.venvs:
        min_idle_days: 90
      node.modules:
        min_idle_days: 180
      rust.target:
        min_idle_days: 60
      python.cache:
        min_idle_days: 90
      system.tmp:
        min_age_days: 30
      git.branches:
        min_idle_days: 90
      cargo.registry:
        min_idle_days: 60
      gradle.caches:
        min_idle_days: 90
      maven.repo:
        min_age_days: 180
    # Repositories for the git.branches plugin
    git_repos:
      - ~/code
      - ~/projects
```

---

## Safety Model

- **Read-only by default** — `audit` and `plan` never touch the filesystem.
- **Dry-run by default** — `clean` without `--apply` only prints a preview.
- **Confidence tiers** — Every candidate is classified as:
  - `SAFE` — Low risk (e.g. dangling Docker image, no project nearby).
  - `REVIEW` — Medium risk (e.g. old download, active project but untouched).
  - `MANUAL` — High risk (e.g. branch on an active project, container in use).
- **Hard exclusions** — System paths and sensitive files are automatically excluded. See [`docs/SAFETY.md`](docs/SAFETY.md) for the full exclusion list.
- **Quarantine** — Deletable files are moved to a quarantine folder so you can `restore` them later.
- **Undo log** — Every `clean --apply` is recorded in SQLite with per-candidate results and undo tokens.
- **Cache-first audits** — Repeated audits skip unchanged paths via the `audit_cache` SQLite table. Modify a file and only that entry invalidates.
- **Per-plugin timeouts** — A hung plugin does not block the entire audit (default 60s).
- **Structured event log** — Every `clean --apply` appends a JSONL event; `bekas history --since 30d` reads from it.
- **Pydantic config validation** — Catches typos at load time with friendly error messages.


---

## Architecture & File Structure

```
bekas/
├── src/bekas/
│   ├── __init__.py              # Package metadata
│   ├── cli.py                   # Click CLI entry point & commands
│   ├── models.py                # Core dataclasses: Candidate, Plan, AuditReport, etc.
│   ├── plugin.py                # Plugin base class & discovery
│   ├── runner.py                # Orchestrates plugin discovery and runs audits
│   ├── clean.py                 # Applies a Plan: removes, quarantines, logs
│   ├── safety.py                # Exclusion engine (path safety, pattern matching)
│   ├── config.py                # Config file I/O and profile resolution
│   ├── formatters.py            # Human, JSON, Markdown output formatters
│   ├── database.py              # SQLite undo log & quarantine registry
│   ├── quarantine.py            # Move / restore / purge quarantined items
│   ├── signing.py               # Plan file signing & verification
│   ├── doctor.py                # Runtime environment diagnostics
│   ├── locking.py               # Single-instance process lock
│   ├── tui.py                   # Textual interactive UI
│   └── plugins/
│       ├── docker_images.py     # Dangling & unused Docker images
│       ├── docker_containers.py # Stopped containers
│       ├── docker_buildx.py     # BuildKit cache
│       ├── python_venvs.py      # Orphaned Python virtual environments
│       ├── python_cache.py      # __pycache__ & tool caches
│       ├── pip_cache.py         # pip download & wheel cache
│       ├── node_modules.py      # Old node_modules directories
│       ├── rust_target.py       # Rust target/ directories
│       ├── cargo_registry.py    # Cargo registry cache
│       ├── go_modcache.py       # Go module cache
│       ├── gradle_caches.py     # Gradle wrapper & dependency caches
│       ├── maven_repo.py        # Old Maven artifact versions
│       ├── git_branches.py      # Stale merged local branches
│       ├── downloads.py         # Old files in ~/Downloads
│       ├── screenshots.py       # Old screenshots
│       ├── system_tmp.py        # Old temp files
│       ├── system_trash.py      # macOS/Linux Trash
│       ├── dotfiles_backups.py  # Dotfile backup files
│       ├── xcode_derived_data.py # Xcode DerivedData (macOS)
│       ├── xcode_simulators.py  # Abandoned Xcode simulators (macOS)
│       ├── uv_cache.py          # UV package cache
│       ├── poetry_cache.py      # Poetry package cache
│       ├── pnpm_store.py        # PNPM global store
│       ├── yarn_cache.py        # Yarn global cache
│       ├── browser_chrome.py    # Chrome/Chromium caches
│       ├── browser_firefox.py   # Firefox caches
│       ├── browser_safari.py    # Safari caches (macOS)
│       ├── vscode_extensions.py # VS Code orphan extensions
│       └── jetbrains_caches.py  # Old JetBrains IDE caches
├── tests/                       # pytest test suite
├── docs/                        # Documentation
│   └── SAFETY.md                # Full safety guide & exclusion rules
├── pyproject.toml               # Project metadata, deps, entry points
└── README.md                    # This file
```

### Key design principles

- **Plugin architecture** — Every scanner is a `Plugin` with an explicit `Capabilities` manifest. The runner loads them via `entry_points`, so third-party plugins can be added without touching core code.
- **Context-driven** — Each plugin receives a `Context` carrying the active profile, dry-run flag, and verbosity. This keeps plugins stateless and testable.
- **Immutable models** — `Candidate`, `Plan`, `RemovalResult`, etc. are dataclasses with strong typing. `AuditReport` uses Pydantic for JSON serialization.
- **Capability manifest** — Plugins declare `quarantine`, `parallel_safe`, `requires_network`, `requires_root`, `requires_cli`, `platforms`, and `estimated_runtime` so the runner can schedule smartly and skip incompatible plugins.
- **Per-plugin timeouts** — A hard timeout (default 60s) per plugin prevents a hung scanner from blocking the entire audit.
- **Audit cache** — `_fingerprint(path)` computes a `(size, mtime)` hash. Unchanged paths are skipped on re-audit; `--no-cache` and `--rebuild-cache` control the behavior.
- **Pydantic config** — `Config`, `Profile`, and `PluginSettings` are Pydantic models. Validation catches typos at load time with friendly per-field errors.
- **Quarantine-first** — Plugins that support quarantine (`capabilities.quarantine=True`) move items to a staging area instead of deleting immediately.
- **Audit trail** — `database.py` writes every run to an SQLite DB and a rotating JSONL event log; `undo` restores quarantined items via their undo tokens.
- **Structured event log** — `events.py` appends JSONL to `~/.local/share/bekas/events.jsonl` with rotation at 10 MB. `bekas history --since 30d` reads from it.

---

## License

MIT
