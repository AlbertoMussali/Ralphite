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
