# bekas

One audit. Every kind of forgotten thing. Nothing deleted you cared about.

`bekas` is a CLI utility that audits your local machine for stuff you forgot — orphaned Docker images, unused git branches, old `node_modules`, dangling Python venvs, stale downloads, screenshots from years ago — and helps you safely reclaim disk space.

## Quick start

```bash
pip install bekas
bekas audit
bekas plan
bekas clean --apply --safe-only
```

## Commands

- `bekas audit` — Run a read-only audit
- `bekas plan` — Preview what clean would do
- `bekas clean` — Remove approved candidates (dry-run by default)
- `bekas inspect <id>` — Show full reasoning for a candidate
- `bekas history` — List past actions
- `bekas undo` — Undo last apply
- `bekas quarantine list` — List quarantined items
- `bekas plugins` — Manage plugins
- `bekas config` — Show current configuration
- `bekas tui` — Launch interactive TUI

## Safety

- Default behavior is read-only
- Hard exclusions protect system paths and sensitive files
- Every destructive action is logged to an SQLite undo log
- Candidates classified as `safe`, `review`, or `manual`

## License

MIT
