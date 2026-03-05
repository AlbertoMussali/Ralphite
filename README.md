# Ralphite

Ralphite is a TUI-first local multi-agent runner with a single YAML plan file.

## Quick Start

### Requirements

- Python 3.12+
- `uv`
- `git`
- `rg`

### Install

```bash
uv sync --all-packages
```

### Initialize

```bash
uv run ralphite init --workspace .
uv run ralphite init --workspace . --yes --template branched --plan-id starter_branched --name "Starter Branched"
```

### Guided first run

```bash
uv run ralphite quickstart --workspace . --no-tui --yes --output stream --bootstrap
```

Optional strict mode:

```bash
uv run ralphite quickstart --workspace . --no-tui --yes --strict-doctor --output table
```

### Run

```bash
uv run ralphite run --workspace . --output stream
```

### Open TUI

```bash
uv run ralphite tui --workspace .
```

### Quality gates

```bash
uv run ralphite doctor --workspace . --output table --fix-suggestions
uv run ralphite validate --workspace . --json
uv run ralphite check --workspace . --full
uv run ralphite check --workspace . --release-gate
uv run ralphite check --workspace . --beta-gate
```

`check --release-gate` is repository-deterministic: it executes named suites from repo root regardless of workspace plan state.
`check --beta-gate` enforces doctor + backend smoke + release suites.

## Headless Backends

Ralphite executes agents through local headless CLIs.

- Default backend: `codex exec`
- Optional backend: `agent` (Cursor headless)
- Default model: `gpt-5.3-codex`
- Default reasoning effort: `medium`

Run-time overrides:

```bash
uv run ralphite run --workspace . --backend codex --model gpt-5.3-codex --reasoning-effort medium
uv run ralphite quickstart --workspace . --backend cursor --model gpt-5.3-codex --reasoning-effort medium --no-tui --yes
```

Fixture confidence suite (included in `--release-gate`):

```bash
uv run --with pytest pytest \
  packages/engine/tests/test_fixture_plan_matrix.py \
  packages/engine/tests/test_dispatched_plan_consistency.py \
  apps/tui/tests/test_bootstrap_e2e.py \
  apps/tui/tests/test_run_setup_resolved_preview_contract.py -q
```

## Canonical Plan (v5)

Ralphite runtime requires `version: 5`.

```yaml
version: 5
plan_id: calculator_cli
name: Calculator CLI Build

materials:
  autodiscover:
    enabled: true
    path: .ralphite/plans
    include_globs: ["**/*.yaml", "**/*.yml", "**/*.md", "**/*.txt"]
  includes: []
  uploads: []

constraints:
  max_parallel: 3
  max_runtime_seconds: 5400
  max_total_steps: 250
  max_cost_usd: 25.0
  fail_fast: true
  acceptance_timeout_seconds: 120
  max_retries_per_node: 0

agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    reasoning_effort: medium
    tools_allow: [tool:*, mcp:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
    reasoning_effort: medium

tasks:
  - id: t1
    title: Define requirements
    completed: false
    routing:
      cell: seq_pre
      tags: [planning]
    acceptance:
      commands: []
      required_artifacts: []
      rubric: ["Requirements are explicit and testable."]

  - id: t2
    title: Implement parser
    completed: false
    deps: [t1]
    routing:
      cell: par_core
      tags: [implementation]
    acceptance:
      commands: ["pytest -q"]
      required_artifacts:
        - id: parser_module
          path_glob: src/parser*.py
          format: text
      rubric: ["Parser covers required inputs."]

orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: prepare_dispatch_default
      kind: prepare_dispatch
      agent: orchestrator_default
      enabled: true
    - id: merge_and_conflict_resolution_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
    - id: summarize_work_default
      kind: summarize_work
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []

outputs:
  required_artifacts:
    - id: final_report
      format: markdown
    - id: machine_bundle
      format: json
```

## Built-in Templates

- `general_sps`: `seq_pre -> orch_merge_1 -> par_core -> orch_merge_2 -> seq_post -> orch_finalize`
- `branched`: trunk prelude, split orchestrator, lane execution, lane merges, join orchestrator
- `blue_red`: per-task `prepare -> blue -> handoff -> red -> merge`, then finalize
- `custom`: explicit cell DSL in `orchestration.custom.cells`

## Validation and Resolved Run Preview

`validate --json` includes resolved execution structure:

- `summary.resolved_execution.template`
- `summary.resolved_execution.resolved_cells`
- `summary.resolved_execution.resolved_nodes`
- `summary.resolved_execution.task_assignment`
- `summary.resolved_execution.compile_warnings`
- `summary.cell_counts`
- `data.recommended_commands` for one-step remediation when available

Run Setup in TUI shows the same resolved run preview before execution.

Dispatch consistency guarantee:

- confidence tests assert that `validate --json` resolved nodes/cells/task assignment match runtime dispatched metadata for canonical fixtures.

Ralphite is v5-only. Plans must use `version: 5`.

## Recovery Contract

`ralphite recover` supports automation:

- `--preflight-only`
- `--resume/--no-resume`
- `--json` or `--output json|table|stream`

Exit codes:

- `0` success
- `10` no recoverable run
- `11` run not found/unrecoverable
- `12` invalid input/mode
- `13` preflight failed
- `14` recovery pending
- `15` terminal failed/cancelled
- `16` internal error

## Playbook

- User-centered operational guide: `docs/USER_CENTERED_PLAYBOOK.md`

## Workspace Layout

- `.ralphite/config.toml`
- `.ralphite/plans/*.yaml` (single source of truth)
- `.ralphite/runs/<run_id>/`
- `.ralphite/artifacts/<run_id>/`
- `.ralphite/worktrees/<run_id>/`

No task sidecar markdown file is required.

## Config Notes

`[run].task_writeback_mode` controls task completion write-back:

- `revision_only` (default): write completed-task revision under `.ralphite/plans`, no commit required
- `in_place`: update and commit active plan path
- `disabled`: skip task completion write-back
