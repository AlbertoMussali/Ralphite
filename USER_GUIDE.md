# Ralphite User Guide (v4 Unified YAML)

## 1) Install (Placeholder)

```bash
uv sync --all-packages
```

Requirements:

- Python 3.12+
- `uv`
- `git`
- `rg`

## 2) Initialize Workspace

```bash
uv run ralphite init --workspace .
```

This creates `.ralphite/` folders and seeds a starter v4 YAML plan.

Quick onboarding path:

```bash
uv run ralphite quickstart --workspace . --no-tui --yes --output stream
```

## 3) Write a Plan

Create or edit `.ralphite/plans/<name>.yaml` using `version: 4`.

Minimum required top-level sections:

- `version`
- `plan_id`
- `name`
- `run`
- `constraints`
- `agents`
- `tasks`

Example:

```yaml
version: 4
plan_id: calc_cli
name: Calculator CLI

run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default

constraints:
  max_parallel: 3
  fail_fast: true

agents:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1
    tools_allow: [tool:*]
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
    title: Define CLI requirements
    completed: false
  - id: t2
    title: Build parser module
    completed: false
    parallel_group: 1
    deps: [t1]
```

## 4) Task Ordering Model

Ordering is fully task-driven:

- list order defines base order
- `parallel_group > 0` creates parallel blocks for consecutive tasks in the same group
- no `parallel_group` (or `0`) means sequential

Validation rules:

- `parallel_group` must be integer `>= 1` when set
- group ids are non-decreasing by first appearance
- group ids must be contiguous (no split/rejoin)
- `deps` can only point to earlier tasks
- cycle detection is enforced

## 5) Run in TUI (Primary)

```bash
uv run ralphite tui --workspace .
```

Flow:

1. open `Run Setup`
2. load a plan
3. review task block preview
4. toggle pre/post orchestrators and adjust constraints
5. validate and save a revision
6. start run
7. monitor `Phase Timeline`
8. use `Recovery` if needed
9. review `Summary`

## 6) CLI (Automation)

Run:

```bash
uv run ralphite run --workspace . --no-tui --output stream
```

Recovery:

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode manual --preflight-only --no-tui --json
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode agent_best_effort --prompt "resolve conflicts" --resume --no-tui --json
```

Validation with fix suggestions:

```bash
uv run ralphite validate --workspace . --json
uv run ralphite validate --workspace . --apply-safe-fixes
```

Recover exit codes:

- `0` success
- `10` no recoverable run
- `11` run not found/unrecoverable
- `12` invalid mode/input
- `13` preflight failed
- `14` recovery pending
- `15` terminal failed/cancelled
- `16` internal error

## 7) Completion Write-Back

After successful post-orchestrator integration, Ralphite handles task completion write-back using `[run].task_writeback_mode`:

- `revision_only` (default): writes a completed-task revision under `.ralphite/plans` and avoids commit failures on ignored paths
- `in_place`: updates and commits the active plan path
- `disabled`: skips task completion write-back

When write-back applies, successful worker tasks are marked with:

- `completed: true`

Write-back commit message:

- `chore(tasks): mark completed for run`

## 8) Checks

```bash
uv run ralphite doctor --workspace . --output table --fix-suggestions
uv run ralphite check --workspace . --full
uv run ralphite check --workspace . --release-gate
```

`--release-gate` is the stabilization gate for parser/compiler, orchestrator integration, TUI tests, and e2e recovery.

## 9) Troubleshooting

Unsupported plan version:

- use `version: 4`

Recovery blocked:

- run `recover --preflight-only --json` and address `blocking_reasons`

Validation failures:

- run `doctor` or use the Run Setup validation panel.
