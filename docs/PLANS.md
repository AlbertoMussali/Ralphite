# Plans Documentation

Owners: schemas, engine
Last verified against commit: a7aaeed

## Canonical Plan Sources

- tracked examples: `examples/plans/*.yaml`
- centralized defaults: `examples/agent_defaults.yaml`
- schema contracts: `src/ralphite/schemas/json/plan-spec.schema.json`

## Starter Templates

- `examples/plans/starter_bugfix.yaml`
- `examples/plans/starter_refactor.yaml`
- `examples/plans/starter_docs_update.yaml`
- `examples/plans/starter_release_prep.yaml`

These are the public job-focused starters. Their underlying orchestration templates stay visible in the YAML, but the top-level product surface is organized around common work types instead of orchestration jargon.

## Worked Examples

- `examples/plans/example_easy_calendar_math_cli.yaml`
- `examples/plans/example_medium_thermal_1d_branched.yaml`

## Local Plan State

- `.ralphite/plans/*.yaml` are workspace-local runtime plans
- not canonical documentation artifacts
