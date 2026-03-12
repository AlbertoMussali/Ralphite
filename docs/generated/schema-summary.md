# Schema Summary (Generated Snapshot)

Owners: schemas
Last verified against commit: 071697a
Generated from:

- `src/ralphite/schemas/json/plan-spec.schema.json`
- `src/ralphite/schemas/json/agent-defaults.schema.json`
- `src/ralphite/schemas/plan.py`

## Agent Schema Snapshot

- provider enum: `codex`, `cursor`
- model default: `gpt-5.3-codex`
- reasoning effort enum: `low`, `medium`, `high` (default `medium`)

## Runtime Version Snapshot

- runtime execution contract: `version: 1`

## Task Write Policy Snapshot

- `allowed_write_roots`: workspace-relative allowlist roots
- `forbidden_write_roots`: workspace-relative denylist roots
- `allow_plan_edits`: default `false`
- `allow_root_writes`: default `false`

## Agent Defaults Snapshot

- defaults schema: `AgentDefaultsSpec` (`version`, `agents`, `behaviors`)
- plan-level defaults reference: optional `agent_defaults_ref`
