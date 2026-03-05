# CLI Contracts

Owners: cli
Last verified against commit: 70b0c1f

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

Execution commands (`run`, `quickstart`, `recover`, `replay`) require the selected workspace to be inside a git worktree. `doctor` surfaces this as the blocking `git-worktree` check.

## Key Override Flags

- `--backend codex|cursor` (`run`, `quickstart`)
- `--model <id>` (`run`, `quickstart`)
- `--reasoning-effort low|medium|high` (`run`, `quickstart`)
- `--strict` (`check`)

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
