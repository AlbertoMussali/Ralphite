# AGENTS Guide

Owners: engine, cli, schemas, release
Last verified against commit: 70b0c1f

## Purpose

This file orients contributors and coding agents to Ralphite's source-of-truth docs, code surfaces, and update rules.

## Repo Navigation

- Product entry: [README.md](README.md)
- Documentation hub: [docs/index.md](docs/index.md)
- Core engine: `packages/engine/src/ralphite_engine/`
- CLI: `apps/cli/src/ralphite_cli/`
- Schemas: `packages/schemas/`
- Canonical starter plans: `examples/plans/`

## Documentation Source-of-Truth Policy

1. Runtime behavior must be documented from code and tests, not inferred intent.
2. Command contracts must align with:
   - `packages/engine/src/ralphite_engine/headless_agent.py`
   - `apps/cli/src/ralphite_cli/app.py`
3. Schema defaults must align with:
   - `packages/schemas/json/plan-spec-v5.schema.json`
   - `packages/schemas/python/src/ralphite_schemas/plan_v5.py`
4. Failure taxonomy must align with:
   - `packages/engine/src/ralphite_engine/taxonomy.py`

## Change Rules

When changing any of these, update docs in the same PR:

- CLI flags / command outputs
- Runtime command templates
- Schema defaults/enums
- Gate suite composition (`check --strict`)
- Recovery semantics or exit codes

## ADR Requirement

For architecture/runtime contract changes or strict-check policy changes, add/update an ADR in `docs/decisions/`.

## Verification Commands

```bash
uv run ruff check .
uv run --with pytest pytest -q
uv run ralphite check --workspace . --strict --output json
```
