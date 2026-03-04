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
uv run ralphite history --workspace .
uv run ralphite replay <RUN_ID> --workspace .
```

### Migration

```bash
uv run ralphite migrate --workspace . --strict
```

`version: 1` plans are deprecated and rejected at validation/runtime boundaries.

## Workspace Layout

Ralphite stores local state in `.ralphite/`:

- `.ralphite/config.toml` local policy/profile
- `.ralphite/plans/` canonical plan files
- `RALPHEX_TASK.md` canonical task source for `version: 2` plans (or custom `task_source.path`)
- `.ralphite/worktrees/` temporary worker/integration worktrees for phase execution
- `.ralphite/runs/<run_id>/run_state.json` persisted run state
- `.ralphite/runs/<run_id>/checkpoint.json` node-level resume checkpoint
- `.ralphite/runs/<run_id>/event_log.ndjson` deterministic event journal
- `.ralphite/artifacts/<run_id>/` output artifacts
