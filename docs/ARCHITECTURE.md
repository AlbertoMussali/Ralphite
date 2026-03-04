# Ralphite Architecture

## Canonical Surfaces

- `apps/tui`: primary UX (run setup, timeline, recovery, summary)
- `apps/tui/cli.py`: automation wrapper around the same engine
- `packages/engine`: local orchestrator, validation, compiler, git/worktree lifecycle, recovery
- `packages/schemas`: shared v5 schema + validation rules

## Plan Contract (v5)

Runtime executes only `version: 5` plans.

Design split:

1. Task definition (`tasks`, including `routing` + `acceptance`).
2. Execution architecture (`orchestration`, templates + behavior catalog).

Required top-level keys:

- `version`, `plan_id`, `name`, `materials`, `constraints`, `agents`, `tasks`, `orchestration`, `outputs`

## Orchestration Templates

Built-ins:

- `general_sps`
- `branched`
- `blue_red`
- `custom`

Behavior catalog:

- `orchestration.behaviors[*]` defines orchestrator roles (`merge_and_conflict_resolution`, `summarize_work`, `prepare_dispatch`, `custom`) and optional agent override.

## Compiler/Runtime Model

Compilation stages:

1. Parse tasks (`task_parser`).
2. Resolve selected template into canonical cells.
3. Expand cells into runtime DAG nodes (workers + intermediate orchestrators).
4. Validate DAG acyclicity/dependencies and emit node levels.

Runtime node metadata includes:

- `cell_id`, `lane`, `team`, `phase`, `behavior_id`

Execution remains DAG/block-aware with:

- `constraints.max_parallel`
- dependency enforcement
- fail-fast handling

## Acceptance Enforcement

Per worker node completion:

1. run `task.acceptance.commands` in task worktree
2. verify `task.acceptance.required_artifacts` globs
3. attach rubric context

Failures are typed runtime failures and participate in fail-fast/recovery behavior.

## Git/Worktree Integration

- workers commit in managed worktrees
- orchestrator merge cells call phase integration
- multiple intermediate merge points per template are supported
- merge conflicts are fail-closed (`paused_recovery_required`)
- recovery modes: `manual`, `agent_best_effort`, `abort_phase`

## Validation and Preview Surfaces

`validate --json` and Run Setup both expose resolved execution structure:

- `template`
- `resolved_cells`
- `resolved_nodes`
- `task_assignment`
- `compile_warnings`

Run Setup is v5-native:

- template/config summary
- routing-aware task table (`lane`, `cell`, `team_mode`)
- resolved run preview before execution

## Migration Strategy

- `ralphite migrate` performs v4 -> v5 conversion
- conversion is idempotent for existing v5 plans
- runtime does not execute v4 directly

## Persistence

Per-run state lives in `.ralphite/runs/<run_id>/`:

- `run_state.json`
- `checkpoint.json`
- `event_log.ndjson`
- `lock`

Artifacts live in `.ralphite/artifacts/<run_id>/`.

## Operator Playbook

User-facing workflows are in `docs/USER_CENTERED_PLAYBOOK.md`.
