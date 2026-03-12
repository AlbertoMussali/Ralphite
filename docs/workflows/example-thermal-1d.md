# Example: Thermal 1D Solver ([medium])

Owners: engine, cli
Last verified against commit: a7aaeed

## Goal

Canonical v1 plan for a simplified 1D thermal stack simulator with:

- Rust core solver (constant properties, constant HTC, fixed boundary conditions)
- Python bindings exposing solver APIs
- wide-format CSV export of simulation outputs

## Why `branched`

The work naturally splits into two lanes after a shared trunk specification:

- `lane_rust`: numerical solver + Rust tests
- `lane_python`: bindings + CSV export/CLI

Then both lanes merge at `trunk_post` for integration and end-to-end verification.

This demonstrates trunk prelude, parallel lane execution, and deterministic trunk merge.

## Task Graph

1. Trunk spec (`task_trunk_spec`)
2. Rust lane (`task_rust_solver_core` -> `task_rust_solver_tests`)
3. Python lane (`task_python_bindings` -> `task_python_csv_export`)
4. Trunk post merge (`task_trunk_integration` -> `task_trunk_verify`)

## Canonical Plan

- `examples/plans/example_medium_thermal_1d_branched.yaml`

## Execute

Validate the plan:

```bash
uv run ralphite validate --workspace . --plan examples/plans/example_medium_thermal_1d_branched.yaml --json
```

Run with real backend:

```bash
uv run ralphite run --workspace . --plan examples/plans/example_medium_thermal_1d_branched.yaml --yes --output stream
```

Run with deterministic local simulation:

```bash
RALPHITE_DEV_SIMULATED_EXECUTION=1 \
RALPHITE_SKIP_BACKEND_CMD_CHECKS=1 \
uv run ralphite run --workspace . --plan examples/plans/example_medium_thermal_1d_branched.yaml --yes --output stream
```

## Expected Outputs

- `final_report` (markdown human summary with outcome, changed files, acceptance results, failures, and next steps)
- `machine_bundle` (json)
- `simulation_wide_csv` (csv)
- task-level artifacts in `rust/thermal_solver/`, `src/thermal_sim/`, and `outputs/`
