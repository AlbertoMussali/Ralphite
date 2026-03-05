# Test Matrix

Owners: engine, tui, release
Last verified against commit: 70b0c1f

## Fast Confidence

- `packages/engine/tests/test_headless_agent.py`: backend command + failure contracts
- `apps/tui/tests/test_cli_ux_commands.py`: CLI gate and backend-smoke command contracts
- `packages/engine/tests/test_examples_plans.py`: tracked starter plans validate/compile

## Release Gate Suites

Defined in `apps/tui/src/ralphite_tui/cli.py` and mirrored in CI:

- parser/compiler
- engine runtime
- tui
- e2e recovery
- fixtures bootstrap (`test_fixture_plan_matrix`, `test_dispatched_plan_consistency`, `test_examples_plans`, `test_bootstrap_e2e`, `test_run_setup_resolved_preview_contract`)

## Strict Release Gate Additions

- doctor must pass
- backend smoke must pass for selected default backend
- release gate suites must pass
