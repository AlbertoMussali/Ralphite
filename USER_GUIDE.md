# Ralphite User Guide (V3, Task-Driven, TUI-First)

This guide covers the v3 hard cutover model:

- task ordering is defined in `RALPHEX_TASK.md` (or your `task_source.path`)
- plan YAML configures phase-level orchestrator controls and constraints
- TUI is the primary UX, CLI is automation/ops

## 1) Install (Placeholder)

Packaging/install channels are still being finalized.

For now, use a source checkout:

```bash
uv sync --all-packages
```

Minimum requirements:

- Python 3.12+
- `uv`
- `git`
- `rg` (ripgrep)

## 2) Initialize Workspace

From repo root:

```bash
uv run ralphite init --workspace .
```

This creates/uses:

- `.ralphite/config.toml`
- `.ralphite/plans/`
- `.ralphite/runs/`
- `.ralphite/artifacts/`
- `RALPHEX_TASK.md` (starter task file if missing)

## 3) Core Model

## 3.1 Task File Is Source of Truth

Execution ordering is driven by markdown task metadata only.

## 3.2 Plan Is Phase Controls + Constraints

Plan v3 configures:

- phase list and order
- pre/post orchestrator toggles per phase
- run constraints (including `max_parallel`)

Plan does not contain worker lane selectors.

## 3.3 Strict Phase Loop

For each phase, runtime executes:

1. optional pre-orchestrator
2. `seq_pre` tasks (source line order)
3. `parallel` tasks (`parallel_group` ascending, then source line order)
4. `seq_post` tasks (source line order)
5. optional post-orchestrator

## 4) Task File Setup

Use markdown checklist items with metadata in HTML comments.

Example:

```md
# Ralphite Tasks

- [ ] Plan scope <!-- id:t1 phase:phase-1 lane:seq_pre agent_profile:worker_default -->
- [ ] Implement API <!-- id:t2 phase:phase-1 lane:parallel parallel_group:1 deps:t1 agent_profile:worker_default -->
- [ ] Implement UI <!-- id:t3 phase:phase-1 lane:parallel parallel_group:1 deps:t1 agent_profile:worker_default -->
- [ ] Integration tests <!-- id:t4 phase:phase-1 lane:parallel parallel_group:2 deps:t2,t3 agent_profile:worker_default -->
- [ ] Final verification <!-- id:t5 phase:phase-1 lane:seq_post deps:t4 agent_profile:worker_default -->
```

Supported metadata:

- `id` (required, stable)
- `phase` (required in practice; defaults to `phase-1`)
- `lane` (`seq_pre | parallel | seq_post`, default `parallel`)
- `parallel_group` (integer `>=1`, only for `lane=parallel`)
- `deps` (CSV task ids)
- `agent_profile`
- `tools` (optional hints)
- `test` (optional hint)

Compatibility parsing:

- legacy `group` is treated as `phase`
- legacy `seq:true` maps to `lane=seq_pre`

Validation rules:

- if any parallel task in a phase sets `parallel_group`, all parallel tasks in that phase must set it
- no cycles
- no dependency on tasks from later phases

Completion behavior:

- after successful phase integration, successful tasks in that phase are marked `[x]` in task file
- write-back commit message format: `chore(tasks): mark completed for <phase-id>`

## 5) Plan YAML (V3 Only)

Ralphite only accepts `version: 3` and `task_source.parser_version: 3`.

Minimal pattern:

```yaml
version: 3
plan_id: my_plan
name: My Plan

task_source:
  kind: markdown_checklist
  path: RALPHEX_TASK.md
  parser_version: 3

agent_profiles:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1
    tools_allow: [tool:*, mcp:*]

  - id: orchestrator_pre_default
    role: orchestrator_pre
    provider: openai
    model: gpt-4.1-mini

  - id: orchestrator_post_default
    role: orchestrator_post
    provider: openai
    model: gpt-4.1-mini

execution_structure:
  phases:
    - id: phase-1
      label: Default Phase
      pre_orchestrator:
        enabled: false
        agent_profile_id: orchestrator_pre_default
      post_orchestrator:
        enabled: true
        agent_profile_id: orchestrator_post_default

constraints:
  max_runtime_seconds: 3600
  max_total_steps: 120
  max_cost_usd: 10.0
  fail_fast: true
  max_parallel: 3
```

Defaults:

- pre-orchestrator: OFF
- post-orchestrator: ON

## 6) TUI-First Flow

Open TUI:

```bash
uv run ralphite tui --workspace .
```

Recommended flow:

1. `Run Setup`
2. load plan
3. review read-only task parse preview (id/phase/lane/group/deps)
4. edit phase orchestrator toggles and constraints only
5. validate and save new timestamped plan revision
6. start run
7. monitor `Phase Timeline`
8. if needed, resolve in `Recovery`
9. inspect `Summary`

Useful keys:

- `ctrl+p` or `:` command palette
- `1..8` screen navigation
- `s/p/r/c` start/pause/resume/cancel

## 7) CLI-Second Flow

Run:

```bash
uv run ralphite run --workspace .
```

Recovery automation:

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode manual --preflight-only --no-tui --json
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode agent_best_effort --prompt "resolve conflicts safely" --resume --no-tui --json
```

Recover flags:

- `--preflight-only`
- `--resume/--no-resume`
- `--json`

Stable recover exit codes:

- `0` success
- `10` no recoverable run
- `11` run not found/unrecoverable
- `12` invalid input/mode
- `13` preflight failed
- `14` recovery pending
- `15` terminal failed/cancelled
- `16` internal error

## 8) Recovery Lifecycle

When integration conflicts occur, run transitions to `paused_recovery_required` and emits `RECOVERY_REQUIRED`.

Modes:

- `manual`
- `agent_best_effort` (requires prompt)
- `abort_phase`

Preflight checks before resume:

- run paused status
- valid selected mode
- prompt present for agent mode
- lock availability
- recovery worktree exists
- unresolved conflict markers cleared

## 9) Git/Worktree Safety

Ralphite uses managed worktrees per worker task and per phase integration.

Behavior:

- preserve worker commits on integration
- fail-closed on unresolved conflicts
- idempotent cleanup for repeated/partial cleanup calls
- stale managed artifacts reported by `doctor` (default threshold: 24h)

## 10) Checks and Release Gate

```bash
uv run ralphite doctor --workspace .
uv run ralphite check --workspace . --full
uv run ralphite check --workspace . --release-gate
```

Release gate runs required suites:

- parser/compiler tests
- orchestrator + git integration tests
- TUI tests
- e2e recovery test

## 11) Troubleshooting

### Unsupported plan version

Use `version: 3` and `task_source.parser_version: 3`.

### Task parse/group errors

Run `doctor` or open Run Setup validation panel; fix invalid task metadata in task file.

### Recovery resume blocked

Use `recover --preflight-only --json` and resolve `blocking_reasons`.

### No task updates written back

Task checkboxes are marked only after successful phase integration.
