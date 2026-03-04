# Ralphite

Ralphite is a TUI-first local multi-agent runner using a single YAML plan file.

## Quick Start

### Requirements

- Python 3.12+
- `uv`
- `git`
- `rg`

### Install (placeholder)

```bash
uv sync --all-packages
```

### Initialize

```bash
uv run ralphite init --workspace .
```

### Guided first run

```bash
uv run ralphite quickstart --workspace . --no-tui --yes --output stream
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
```

## Canonical Plan (v4 Only)

Ralphite accepts only `version: 4` plans.

```yaml
version: 4
plan_id: calculator_cli
name: Calculator CLI Build

run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default

constraints:
  max_parallel: 3
  max_runtime_seconds: 5400
  max_total_steps: 250
  max_cost_usd: 25.0
  fail_fast: true

agents:
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

tasks:
  - id: t1
    title: Define requirements
    completed: false
  - id: t2
    title: Scaffold project
    completed: false
  - id: t3
    title: Implement parser
    completed: false
    parallel_group: 1
    deps: [t2]
```

## Runtime Semantics

Execution is single-phase and block-based from `tasks` list order:

1. optional pre-orchestrator
2. sequential block(s): tasks with `parallel_group` missing or `0`
3. parallel block(s): consecutive tasks sharing same `parallel_group > 0`
4. optional post-orchestrator

Rules:

- `parallel_group` must be integer `>= 1` when set.
- group first appearance must be non-decreasing.
- a group can appear in only one contiguous block.
- `deps` must reference earlier tasks only.

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

`[run].task_writeback_mode` controls how task completion write-back is handled:

- `revision_only` (default): write a completed-task revision under `.ralphite/plans`, no commit required.
- `in_place`: update and commit the active plan path.
- `disabled`: skip task completion write-back.
