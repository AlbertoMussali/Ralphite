# Ralphite User Guide (v5)

## 1) Install

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
uv run ralphite init --workspace . --yes --template blue_red --plan-id starter_blue_red --name "Starter Blue Red"
```

This creates `.ralphite/` folders and bootstraps a v5 plan using the selected template.

Quick onboarding path:

```bash
uv run ralphite quickstart --workspace . --no-tui --yes --output stream --bootstrap
```

For strict environment gating, add `--strict-doctor`.

## 3) Author a v5 Plan

Create or edit `.ralphite/plans/<name>.yaml` using `version: 5`.

Required top-level sections:

- `version`
- `plan_id`
- `name`
- `materials`
- `constraints`
- `agents`
- `tasks`
- `orchestration`
- `outputs`

Task authoring model:

1. Define tasks (`id`, `title`, `deps`, `routing`, `acceptance`).
2. Select an orchestration template (`general_sps`, `branched`, `blue_red`, `custom`) and configure it.

## 4) Built-in Orchestration Templates

### `general_sps`

Default built-in flow:

- `seq_pre -> orch_merge_1 -> par_core -> orch_merge_2 -> seq_post -> orch_finalize`

Inference mode is mixed:

- explicit `routing.cell` wins
- otherwise SPS defaults unmatched tasks into `seq_pre`

### `branched`

Flow:

- trunk prelude
- split orchestrator
- user lanes (`orchestration.branched.lanes`)
- lane-local orchestrators
- join orchestrator

Routing rules:

- set `routing.lane` for lane work
- or mark trunk work with `routing.group: trunk`
- unknown lanes fail validation

### `blue_red`

Per selected task unit:

- `prepare -> blue worker -> handoff -> red worker -> merge/summarize`

Used for implement-then-audit loops.

### `custom`

Use `orchestration.custom.cells` with typed cell kinds (`sequential`, `parallel`, `orchestrator`, `split`, `join`, `team_cycle`).

No aggressive inference.

## 5) Run Setup (TUI)

```bash
uv run ralphite tui --workspace .
```

Flow:

1. Open `Run Setup`.
2. Load a plan.
3. Review template/config summary.
4. Set template/config directly (`template`, `branched.lanes`, `blue_red.loop_unit`).
5. Edit task routing fields (`lane`, `cell`, `team_mode`) and task deps/agent/completion.
6. Use validation badges (`Title`, `Deps`, `Agent`, `Routing`, `Acceptance`) for row-level issues.
7. Optionally apply safe fixes and inspect diff preview (`Accept` / `Reject`).
8. Review **Resolved Run Preview** (`order | cell | lane | role | task_id`) with compact/verbose toggle.
9. Validate and save revision.
10. Start run.

## 6) CLI

Run:

```bash
uv run ralphite run --workspace . --no-tui --output stream
```

Validate with resolved execution payload:

```bash
uv run ralphite validate --workspace . --json
```

Run fixture confidence suite locally:

```bash
uv run --with pytest pytest \
  packages/engine/tests/test_fixture_plan_matrix.py \
  packages/engine/tests/test_dispatched_plan_consistency.py \
  apps/tui/tests/test_bootstrap_e2e.py \
  apps/tui/tests/test_run_setup_resolved_preview_contract.py -q
```

Run deterministic release gate (repo-root suites, independent of workspace plan state):

```bash
uv run ralphite check --workspace . --release-gate --output json
uv run ralphite check --workspace . --beta-gate --output json
```

Headless backend overrides:

```bash
uv run ralphite run --workspace . --backend codex --model gpt-5.3-codex --reasoning-effort medium --no-tui --output stream
uv run ralphite quickstart --workspace . --backend cursor --model gpt-5.3-codex --reasoning-effort medium --no-tui --yes
```

Exact backend command contracts:

```bash
codex exec --json --ephemeral --skip-git-repo-check --cd <worktree> --model gpt-5.3-codex -c 'model_reasoning_effort="medium"' -c 'approval_policy="never"' --sandbox workspace-write "<prompt>"
agent -p --force --output-format json --model gpt-5.3-codex "<prompt>"
```

Beta defaults and compatibility:

- Default execution backend is `codex`.
- Cursor is optional unless explicitly selected.
- `provider: openai` is legacy/warn-only and normalized to codex behavior.

Recovery:

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode manual --preflight-only --no-tui --json
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode agent_best_effort --prompt "resolve conflicts" --resume --no-tui --json
```

Machine-readable JSON envelopes (`schema_version: cli-output.v1`) are available for:

- `quickstart`, `validate`, `doctor`, `run`, `recover`, `history`, `replay`, `check`
- `tui` supports JSON only with `--dry-run`

Recover exit codes:

- `0` success
- `10` no recoverable run
- `11` run not found/unrecoverable
- `12` invalid mode/input
- `13` preflight failed
- `14` recovery pending
- `15` terminal failed/cancelled
- `16` internal error

## 7) Validation Diagnostics

`validate --json` includes:

- schema/contract issues
- behavior resolution issues (unknown behavior/agent)
- routing issues (unmapped tasks for template requirements)
- `summary.resolved_execution` with resolved cells/nodes/assignment/warnings
- `data.recommended_commands` with direct fix commands when applicable
- `summary.cell_counts`
- dispatched-plan consistency is covered in fixture E2E tests (validation graph == runtime metadata graph)

## 8) Completion Write-Back

After successful run completion, task completion write-back follows `[run].task_writeback_mode`:

- `revision_only` (default): writes completed-task revision under `.ralphite/plans`
- `in_place`: updates and commits active plan path
- `disabled`: skips task completion write-back

## 9) Troubleshooting

Unsupported plan version:

- runtime accepts only `version: 5`
- re-author the plan in `version: 5` format

Recovery blocked:

- run `recover --preflight-only --json` and address `blocking_reasons`

Validation failures:

- inspect `validate --json` issues and resolved execution block
- use Run Setup badges and safe-fix preview

Canonical starter plans:

- tracked examples live under `examples/plans/`
- local `.ralphite/plans/` files remain user workspace copies

## 10) User-Centered Playbook

- See `docs/USER_CENTERED_PLAYBOOK.md` for canonical user flows, automation examples, and troubleshooting language.
- See `docs/BETA_RELEASE_CHECKLIST.md` for pre-release command gates and sign-off artifact requirements.
