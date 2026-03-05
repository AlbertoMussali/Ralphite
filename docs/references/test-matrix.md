# Test Matrix

Owners: engine, cli, release
Last verified against commit: 70b0c1f

## Fast Confidence

- `tests/engine/test_headless_agent.py`: backend command + failure contracts
- `tests/cli/test_cli_ux_commands.py`: CLI gate and backend-smoke command contracts
- `tests/engine/test_examples_plans.py`: tracked starter plans validate/compile

## Strict Check Suites

Defined in `src/ralphite/cli/commands/check_cmd.py` and mirrored in CI:

- parser/compiler
- engine runtime
- cli-contract
- e2e recovery
- fixtures bootstrap (`test_fixture_plan_matrix`, `test_dispatched_plan_consistency`, `test_examples_plans`, `test_bootstrap_e2e`)

## Strict Mode Additions

- doctor must pass
- backend smoke must pass for selected default backend
- strict check suites must pass
