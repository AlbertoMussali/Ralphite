# Pre-Unification Baseline Notes

Captured: 2026-03-05

## CI Reference

- Latest observed main CI run before refactor: [22705377607](https://github.com/AlbertoMussali/Ralphite/actions/runs/22705377607)
- Conclusion: failure (quick-check contract tests)

## Commands

- `uv sync --all-packages --dev --python 3.13 --no-editable && uv run --no-sync --python 3.13 pytest -q`
- `RALPHITE_DEV_SIMULATED_EXECUTION=1 RALPHITE_SKIP_BACKEND_CMD_CHECKS=1 python3 scripts/bench_cli.py --repeats 5 --output benchmarks/baseline/pre_unification_v1.json`

## Baseline Source

- `benchmarks/baseline/pre_unification_v1.json` is normalized to the committed pre-pivot reference (`benchmarks/baseline/pre_pivot_cli.json`) so perf comparisons stay anchored to pre-refactor numbers.

## Artifacts

- `benchmarks/baseline/pre_unification_tests.txt`
- `benchmarks/baseline/pre_unification_v1.json`
- `benchmarks/baseline/pre_unification_env.json`
