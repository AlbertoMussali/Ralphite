# Plan v1 Schema Reference

Owners: schemas
Last verified against commit: 071697a

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

## Task Write Policy

- `tasks[].write_policy.allowed_write_roots`: workspace-relative roots the task may mutate
- `tasks[].write_policy.forbidden_write_roots`: workspace-relative roots the task may not mutate
- `tasks[].write_policy.allow_plan_edits`: defaults to `false`
- `tasks[].write_policy.allow_root_writes`: defaults to `false`

Runtime notes:

- If `allowed_write_roots` is omitted, Ralphite derives a conservative allowlist from declared acceptance artifact roots when possible.
- Plan edits are rejected unless `allow_plan_edits: true`.
- `forbidden_write_roots` takes precedence over `allowed_write_roots`.
- Observed local writes outside scope are rejected as `backend_out_of_worktree_mutation`.
