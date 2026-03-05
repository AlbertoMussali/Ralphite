# Plan v1 Schema Reference

Owners: schemas
Last verified against commit: 70b0c1f

Source files:

- `packages/schemas/json/plan-spec.schema.json`
- `packages/schemas/python/src/ralphite_schemas/plan.py`

## Required Top-Level Sections

- `version`, `plan_id`, `name`, `materials`, `constraints`, `agents`, `tasks`, `orchestration`, `outputs`

## Agent Defaults

- `provider: codex` (supported providers: `codex`, `cursor`)
- `model: gpt-5.3-codex`
- `reasoning_effort: medium`

## Orchestration Templates

- `general_sps`, `branched`, `blue_red`, `custom`

## Runtime Compatibility

- execution supports only `version: 1`
