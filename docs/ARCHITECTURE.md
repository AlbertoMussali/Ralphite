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
- `recovery_preflight(run_id) -> {ok, checks, blocking_reasons, conflict_files, next_commands, ...}`
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
- Task definition remains file-sourced; TUI edits execution structure (phase toggles/lane selectors) and recovery behavior, not task content.
- Run Setup persists edits as timestamped plan revisions; source plan files are not overwritten in place by default.

## Recovery Preflight Lifecycle

1. Conflict during integration emits `RECOVERY_REQUIRED` and run transitions to `paused_recovery_required`.
2. User/operator selects recovery mode: `manual`, `agent_best_effort`, or `abort_phase`.
3. `recovery_preflight` validates:
   - paused status
   - selected mode validity
   - prompt presence for `agent_best_effort`
   - lock availability
   - recovery worktree availability
   - unresolved conflict markers in reported conflict files
4. Preflight failures emit `RECOVERY_PREFLIGHT_FAILED`, set `recovery.status=preflight_failed`, and keep run paused.
5. Successful resume emits `RECOVERY_RESUMED` and execution continues from checkpoint.

## Git/Worktree Safety Policy

- Worker tasks execute in managed worktrees under `.ralphite/worktrees/<run_id>/...`.
- Integration preserves worker commits (no forced squash).
- Merge conflict behavior is fail-closed; unresolved conflicts require explicit recovery action.
- Cleanup is idempotent for already-removed worktrees/branches.
- Stale managed artifacts are detected by run id and age threshold (default `24h`) and surfaced via doctor/reports.
