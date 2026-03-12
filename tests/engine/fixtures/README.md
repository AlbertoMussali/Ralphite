# Engine Test Fixtures

This directory contains canonical fixture assets for confidence tests that validate install-to-run behavior.

## Layout

- `plans/`: versioned plan fixtures used across validation, compile, and runtime dispatch consistency tests.
- `configs/`: representative local profile config fixtures.

## Plan Fixtures

- `general_sps_minimal.yaml`: baseline sequential-parallel-sequential orchestration.
- `branched_two_lane.yaml`: branched split/join flow with two lanes and trunk post step.
- `blue_red_per_task.yaml`: per-task blue/red cycle orchestration.
- `custom_linear_cells.yaml`: explicit custom cell DSL flow (`pre -> merge -> post`).
- `invalid_v1_routing.yaml`: invalid v1 fixture used to assert routing diagnostics.

## Fixture Contract

Fixtures must remain small, deterministic, and portable so they can run in CI quickly. They are intentionally representative rather than exhaustive and are designed to mirror user-authored starter plans.
