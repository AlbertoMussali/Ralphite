# AGENTS Guide

Owners: engine, cli, schemas, release
Last verified against commit: 70b0c1f

## Purpose

This file orients contributors and coding agents to Ralphite's source-of-truth docs, code surfaces, and update rules.

## Repo Navigation

- Product entry: [README.md](README.md)
- Documentation hub: [docs/index.md](docs/index.md)
- Core engine: `src/ralphite/engine/`
- CLI: `src/ralphite/cli/`
- Schemas: `src/ralphite/schemas/`
- Canonical starter plans: `examples/plans/`

## Documentation Source-of-Truth Policy

1. Runtime behavior must be documented from code and tests, not inferred intent.
2. Command contracts must align with:
   - `src/ralphite/engine/headless_agent.py`
   - `src/ralphite/cli/app.py`
3. Schema defaults must align with:
   - `src/ralphite/schemas/json/plan-spec.schema.json`
   - `src/ralphite/schemas/plan.py`
4. Failure taxonomy must align with:
   - `src/ralphite/engine/taxonomy.py`

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
uv run --no-sync pytest -q
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json
```
