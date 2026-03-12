# Test Matrix

Owners: engine, cli, release
Last verified against commit: 071697a

## Fast Confidence

- `tests/engine/test_headless_agent.py`: backend command + failure contracts
- `tests/engine/test_plan_defaults_resolution.py`: centralized defaults resolution + override precedence
- `tests/cli/test_cli_ux_commands.py`: CLI gate and backend-smoke command contracts
- `tests/engine/test_examples_plans.py`: all tracked canonical plans (`examples/plans/*.yaml`, including starter + app examples) validate/compile
- `tests/engine/test_orchestrator.py`: authority inversion, write-scope enforcement, salvage promotion, and recovery behavior
- `tests/engine/test_docs_knowledge_base.py`: required docs, freshness markers, generated snapshots, and key contract strings

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
