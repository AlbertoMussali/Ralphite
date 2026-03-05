# CLI Contracts

Owners: tui
Last verified against commit: 70b0c1f

Source file: `apps/tui/src/ralphite_tui/cli.py`

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
- `tui`

## Key Override Flags

- `--backend codex|cursor` (`run`, `quickstart`)
- `--model <id>` (`run`, `quickstart`)
- `--reasoning-effort low|medium|high` (`run`, `quickstart`)
- `--release-gate` (`check`)

## JSON Envelope

- `schema_version: cli-output.v1`
- `command`, `ok`, `status`, `run_id`, `exit_code`, `issues`, `next_actions`, `data`
