# CLI Contracts

Owners: cli
Last verified against commit: 70b0c1f

Source file: `apps/cli/src/ralphite_cli/app.py`

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
