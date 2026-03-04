# Ralphite Architecture

## Canonical Architecture

- `apps/tui`: terminal app shell with global command palette and multi-screen navigation
- `packages/engine`: local orchestrator, validation/autofix, migration, durable run persistence
- `packages/schemas`: plan contracts and validation rules

## Local Runtime Flow

1. `ralphite init` creates `.ralphite` layout and runs strict migration gate.
2. `ralphite run` runs strict migration preflight, resolves/creates plan, and starts a local run.
3. Orchestrator writes event journal, run state, and checkpoints under `.ralphite/runs/<run_id>/`.
4. TUI shell displays run board, timeline, artifacts, history, and editor in one keyboard-first surface.
5. On interruption, `ralphite recover` rehydrates from checkpoint and resumes node-level execution.

## Engine Interface

- `start_run(plan_ref|plan_content)`
- `stream_events(run_id)`
- `pause_run(run_id)`
- `resume_run(run_id)`
- `cancel_run(run_id)`
- `rerun_failed(run_id)`
- `recover_run(run_id)`
- `resume_from_checkpoint(run_id)`
- `list_recoverable_runs()`
- `load_run_state(run_id)`

## Persistence Contracts

Per run directory `.ralphite/runs/<run_id>/`:

- `run_state.json` (`RunPersistenceState`)
- `checkpoint.json` (`RunCheckpoint`)
- `event_log.ndjson` (`EventJournalRecord`)
- `lock` (single-writer run lock)

## TUI Navigation Contracts

- App shell uses a screen stack with top navigation.
- Global command palette (`ctrl+p` / `:`) is authoritative for discoverability.
- Editor is form-first and only exposes supported plan primitives (`agent`, `gate`, retry loops).
