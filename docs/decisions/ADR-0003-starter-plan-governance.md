# ADR-0003: Starter Plan Governance

Last verified against commit: 071697a

- Status: Accepted
- Date: 2026-03-05
- Owners: schemas, engine, release

## Context

Local `.ralphite/plans` are workspace state and not suitable as canonical docs artifacts.

## Decision

- Canonical starter plans are tracked in `examples/plans/`.
- CI validates/compiles these examples.
- Docs reference tracked examples as source-of-truth templates.

## Alternatives Considered

1. Keep canonical examples only under gitignored `.ralphite/plans`.
2. Generate examples dynamically only.
3. Track only one template example.

## Consequences

- Better reviewability and reproducibility.
- Requires maintaining examples alongside schema/runtime changes.

## Rollback / Migration Plan

If examples drift, update examples and tests in same PR; do not reintroduce gitignored canonical plan references.

## References

- `examples/plans/`
- `tests/engine/test_examples_plans.py`
