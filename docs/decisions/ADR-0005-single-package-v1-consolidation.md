# ADR-0005: Single-Package v1 Consolidation

Owners: release, cli, engine, schemas
Status: accepted
Date: 2026-03-05

## Context

Ralphite previously shipped as three workspace packages:

- `ralphite-cli`
- `ralphite-engine`
- `ralphite-schemas`

This split increased install/deploy complexity and required workspace-specific CI/install flows.
Recent contract hardening also standardized plan runtime to `version: 1`, making a unified v1 runtime boundary practical.

## Decision

Adopt a single distributable package:

- `ralphite==1.0.0`

Consolidate repo/package layout to:

- `src/ralphite/{cli,engine,schemas}`
- `tests/{cli,engine,perf}`
- `docs/`
- `scripts/`

Hard-break old Python import paths with no compatibility shims:

- remove `ralphite_cli.*`, `ralphite_engine.*`, `ralphite_schemas.*`
- use `ralphite.cli.*`, `ralphite.engine.*`, `ralphite.schemas.*`

Preserve CLI contract and v1 plan/schema contract unless explicitly version-bumped in a future ADR.

## CI and Tooling Policy

CI must run as a single-project install flow:

- `uv sync --dev --python 3.13 --no-editable`

CI must keep:

- `ruff format --check`
- `ruff check`
- `ruff check --fix` cleanliness gate (`git diff --exit-code`)
- full test suite
- strict check gate
- perf regression gate (`<=10%` runtime/RSS)

CI must include deterministic preflight for required binaries used by doctor/check (notably `rg`).

## Consequences

Positive:

- simpler installation and release process for users
- one version stream and clearer runtime contract boundary
- fewer workspace-specific edge cases in CI

Tradeoffs:

- Python import path break for internal consumers
- migration effort across tests/docs/tooling references

Out of scope:

- introducing Python API compatibility guarantees; CLI remains the public contract
