# Ralphite

Ralphite is a terminal-first local orchestration platform for solo builders.

## Product Surface

- `apps/tui`: `ralphite` CLI + multi-screen Textual shell (canonical UX)
- `packages/engine`: in-process local orchestrator with durable checkpoints and recovery
- `packages/schemas`: shared plan/event schemas

## Quick Start

### Requirements

- Python 3.12+
- `uv`

### Install

```bash
uv sync --all-packages
```

### Initialize workspace

```bash
uv run ralphite init --workspace .
```

### Run checks

```bash
uv run ralphite doctor --workspace .
uv run ralphite check --workspace . --full
uv run ralphite check --workspace . --release-gate
```

### Start run

```bash
uv run ralphite run --workspace .
```

### Open TUI shell

```bash
uv run ralphite tui --workspace .
```

### Recovery, history, replay

```bash
uv run ralphite recover --workspace .
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode manual --preflight-only --no-tui --json
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode agent_best_effort --prompt "resolve merge conflicts safely" --resume --no-tui --json
uv run ralphite history --workspace .
uv run ralphite replay <RUN_ID> --workspace .
```

### Migration

```bash
uv run ralphite migrate --workspace . --strict
```

`migrate` is deprecated in v3-only mode and exits with guidance.

Ralphite accepts only `version: 3` plans and rejects older plan versions at validation/runtime boundaries.

## V3 Execution Model

- Task ordering is defined in `RALPHEX_TASK.md` via task metadata (`phase`, `lane`, `parallel_group`, `deps`).
- Plan files configure phase-level controls only (pre/post orchestrator toggles + constraints).
- Phase execution order is deterministic: optional pre-orchestrator -> `seq_pre` -> grouped `parallel` -> `seq_post` -> optional post-orchestrator.
- After successful phase integration, completed tasks are written back to the task file as checked items.

## Recovery Automation Contract

`ralphite recover` supports machine-oriented workflows:

- `--preflight-only`: run recovery readiness checks and exit.
- `--resume/--no-resume`: explicitly control resume behavior after selecting a mode.
- `--json`: emit structured JSON payloads for scripts/CI.

Stable exit codes:

- `0`: success
- `10`: no recoverable run
- `11`: run not found or unrecoverable
- `12`: invalid recovery mode/input
- `13`: recovery preflight failed
- `14`: recovery still pending (for example `--no-resume` or resume rejected)
- `15`: run reached terminal failed/cancelled state
- `16`: internal error/unexpected state

Preflight output includes `checks`, `blocking_reasons`, `conflict_files`, and suggested `next_commands`.

## Release Gate

`ralphite check --release-gate` runs the v3 stabilization suites and fails closed:

- parser/compiler unit tests
- orchestrator + git/worktree integration tests
- TUI command/screen tests
- e2e recovery scenario

CI enforces this gate using the same command.

## Doctor Stale Artifact Policy

`ralphite doctor` reports managed stale artifacts under `.ralphite/worktrees` and managed `ralphite/*` branches by run id.

- default stale threshold: `24` hours
- stale entries are warnings, with actionable cleanup hints
- cleanup paths/branches are idempotent and safe to re-run

## Workspace Layout

Ralphite stores local state in `.ralphite/`:

- `.ralphite/config.toml` local policy/profile
- `.ralphite/plans/` canonical plan files
- `RALPHEX_TASK.md` canonical task source for `version: 3` plans (or custom `task_source.path`)
- `.ralphite/worktrees/` temporary worker/integration worktrees for phase execution
- `.ralphite/runs/<run_id>/run_state.json` persisted run state
- `.ralphite/runs/<run_id>/checkpoint.json` node-level resume checkpoint
- `.ralphite/runs/<run_id>/event_log.ndjson` deterministic event journal
- `.ralphite/artifacts/<run_id>/` output artifacts
