# ADR-0006: Centralized Agent Defaults for Plan v1

- Status: Accepted
- Date: 2026-03-05
- Owners: engine, schemas, release

## Context

Plan v1 embedded agent role profiles and orchestration behavior prompts directly in each plan file. This duplicated defaults across tracked plans and made iterative prompt tuning noisy and inconsistent.

## Decision

- Add optional `agent_defaults_ref` to Plan v1.
- Introduce a repo-tracked defaults document (`version: 1`) that contains:
  - `agents: list[AgentSpec]`
  - `behaviors: list[BehaviorSpec]`
- Materialize effective plan data by applying defaults before plan model validation:
  - if plan `agents` is non-empty, keep inline values
  - otherwise inject defaults `agents`
  - if plan `orchestration.behaviors` is non-empty, keep inline values
  - otherwise inject defaults `behaviors`
- Missing/unreadable/invalid defaults references fail validation fast.
- Prompt placeholders use strict `{{token}}` templates with role-scoped token sets; invalid placeholders fail validation/runtime.

## Alternatives Considered

1. Keep `agents` required and only use centralized defaults during `init`.
2. Introduce Plan v2 only for centralized defaults.
3. Allow permissive placeholder rendering with warnings.

## Consequences

- Plan files can centralize role/personality/prompt defaults without losing per-plan override flexibility.
- Validation/runtime now enforce stricter prompt-template correctness.
- Existing inline plans remain compatible.

## Rollback / Migration Plan

If centralized defaults cause instability, remove `agent_defaults_ref` from affected plans and provide inline `agents` + `orchestration.behaviors` directly in plan YAML.

## References

- `src/ralphite/engine/plan_defaults.py`
- `src/ralphite/schemas/plan.py`
- `src/ralphite/schemas/json/plan-spec.schema.json`
- `src/ralphite/schemas/json/agent-defaults.schema.json`
