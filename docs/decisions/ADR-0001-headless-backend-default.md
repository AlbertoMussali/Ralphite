# ADR-0001: Headless Backend Default

- Status: Accepted
- Date: 2026-03-05
- Owners: engine, release

## Context

Runtime moved from simulation-centric behavior to explicit headless backend execution for production truthfulness.

## Decision

- Default backend is codex.
- Cursor backend remains supported as optional.
- Runtime command contracts are centralized in command builders.

## Alternatives Considered

1. Keep simulation as default runtime path.
2. Keep OpenAI SDK provider as default runtime backend.
3. Remove cursor support entirely.

## Consequences

- Better production alignment and observability.
- Requires local backend CLI readiness.
- Stronger contract testing needed to prevent drift.

## Rollback / Migration Plan

If backend regressions occur, gate releases until contract/health checks pass. Do not silently restore simulation defaults for strict strict-check runs.

## References

- `packages/engine/src/ralphite_engine/headless_agent.py`
- `apps/cli/src/ralphite_cli/app.py`
- `packages/engine/tests/test_headless_agent.py`
