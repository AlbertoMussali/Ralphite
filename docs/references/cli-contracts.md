# CLI Contracts

Owners: cli
Last verified against commit: a8f4411

Source file: `src/ralphite/cli/app.py`

## Core Commands

- `init`
- `quickstart`
- `run`
- `validate`
- `doctor`
- `check`
- `recover`
- `history`
- `replay`
- `salvage`
- `cleanup`
- `reconcile`
- `promote-salvage`

Execution commands (`run`, `quickstart`, `recover`, `replay`) require the selected workspace to be inside a git worktree. `run`, `quickstart`, and `replay` also fail closed when recoverable runs or stale managed Ralphite worktrees/branches are still present. `doctor` surfaces the underlying git/runtime hygiene checks. `salvage` and `cleanup` can inspect and remove preserved/orphaned managed git artifacts even when `.ralphite/runs/<run-id>` state is partially missing. `reconcile` rebuilds a run summary from persisted state plus live git metadata and `reconcile --apply` persists repaired cached state. `promote-salvage` can promote retained committed worker work and dirty retained work after local acceptance plus an orchestrator-created commit. Stream-oriented CLI output falls back to plain ASCII-safe text if the host console cannot encode Rich output.

Operational notes:

- `reconcile --apply` is the supported operator path for persisting repaired cached node/phase state.
- `salvage` rows now include salvage class values such as `dirty_uncommitted`, `committed_unmerged`, and `orphan_managed_artifact`.
- `promote-salvage` is valid for retained committed work and for dirty retained work that can pass local acceptance and be committed by Ralphite.
- Backend protocol failures distinguish missing final payloads from malformed payloads (`backend_payload_missing`, `backend_payload_malformed`).
- Recovery output for blocked base integration may include `ignored_overlap_files` when only bookkeeping surfaces were tolerated.
- Recovery/conflict metadata may include `resolver_attempted`, `resolved_files`, `unsupported_conflict_files`, and `auto_resolved_conflicts` for deterministic merge repair attempts.

## Key Override Flags

- `--backend codex|cursor` (`run`, `quickstart`)
- `--model <id>` (`run`, `quickstart`)
- `--reasoning-effort low|medium|high` (`run`, `quickstart`)
- `--first-failure-recovery none|agent_best_effort` (`run`, `quickstart`)
- `--strict` (`check`)
- `init --template` prefers job-shaped starters, and still accepts `general_sps | branched | blue_red | custom` for compatibility
- `init` table output includes `Init selections`, `Workspace state`, and `Next steps` sections to make defaults and first actions explicit

## JSON Envelope

- `schema_version: cli-output.v1`
- `command`, `ok`, `status`, `run_id`, `exit_code`, `issues`, `next_actions`, `data`

## Additive `data` Fields

The envelope version remains `cli-output.v1`. New product polish fields are additive under `data`.

For `doctor`, `quickstart`, and `run`, `data.execution_summary` may include:

- `plan_path`
- `backend`
- `model`
- `reasoning_effort`
- `capabilities`
- `duration_seconds`
- `artifacts_count`

`capabilities` is a summary object intended for operator-facing UX and may include per-group summaries for tools and MCP servers.

`data.git` may be present on fail-closed execution responses and includes the workspace git readiness detail used for the operator-facing error.

`data.run_start_preflight` may be present on blocked `run`, `quickstart`, or `replay` responses and includes the recoverable run ids, stale artifact detail, and suggested next commands.
