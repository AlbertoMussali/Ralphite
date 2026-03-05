# Plan v1 Schema Reference

Owners: schemas
Last verified against commit: 70b0c1f

Source files:

- `src/ralphite/schemas/json/plan-spec.schema.json`
- `src/ralphite/schemas/plan.py`

## Required Top-Level Sections

- `version`, `plan_id`, `name`, `materials`, `constraints`, `tasks`, `orchestration`, `outputs`

## Optional Top-Level Sections

- `agent_defaults_ref`: path to a defaults document (`version: 1`) with `agents` + `behaviors`
- `agents`: inline agent overrides (authoritative when non-empty)

## Agent Defaults

- `provider: codex` (supported providers: `codex`, `cursor`)
- `model: gpt-5.3-codex`
- `reasoning_effort: medium`

## Resolution Precedence

1. If `agents` is non-empty, use inline `agents`; otherwise inject from `agent_defaults_ref`.
2. If `orchestration.behaviors` is non-empty, use inline behaviors; otherwise inject from `agent_defaults_ref`.

## Orchestration Templates

- `general_sps`, `branched`, `blue_red`, `custom`

## Runtime Compatibility

- execution supports only `version: 1`
