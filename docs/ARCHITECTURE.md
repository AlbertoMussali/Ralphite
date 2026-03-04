# Ralphite Architecture

## Canonical Architecture

- `apps/tui`: terminal app shell with global command palette and multi-screen navigation
- `packages/engine`: local orchestrator, v2 validation, worktree lifecycle, durable run persistence
- `packages/schemas`: plan contracts and validation rules

## Local Runtime Flow

1. `ralphite init` creates `.ralphite` layout and runs strict migration gate.
2. `ralphite run` runs strict migration preflight, resolves/creates plan, and starts a local run.
3. `version: 2` plans read canonical tasks from `task_source.path` (`RALPHEX_TASK.md` by default) and compile them into a runtime DAG from phase structure (`seq_pre -> parallel -> seq_post` + optional pre/post orchestrators).
4. Worker tasks run in per-task worktrees; post-orchestrator integrates phase output back to base branch preserving worker commits.
5. On merge conflicts, run enters `paused_recovery_required` and exposes manual / best-effort-agent / abort recovery modes.
6. Orchestrator writes event journal, run state, checkpoints, and summary artifacts under `.ralphite/`.
7. TUI shell focuses on run setup, phase timeline, recovery console, and post-run summary.

## Engine Interface

- `start_run(plan_ref|plan_content)`
- `stream_events(run_id)`
- `pause_run(run_id)`
- `resume_run(run_id)`
- `cancel_run(run_id)`
- `rerun_failed(run_id)`
- `recover_run(run_id)`
- `set_recovery_mode(run_id, mode, prompt?)`
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
- TUI is execution-first: `Run Setup` -> `Phase Timeline` -> `Recovery` (if needed) -> `Summary`.
- Task definition remains file-sourced; TUI edits execution flow and recovery behavior, not graph nodes.
