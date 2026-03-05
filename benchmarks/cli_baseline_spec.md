# CLI Baseline Spec

This document defines the stable CLI performance baseline protocol.

## Environment

- Run from repo root.
- Set `RALPHITE_DEV_SIMULATED_EXECUTION=1`.
- Set `RALPHITE_SKIP_BACKEND_CMD_CHECKS=1`.
- Use the same machine class for before/after comparison.

## Commands

- `uv run ralphite quickstart --workspace <tmp> --bootstrap --yes --output json`
- `uv run ralphite check --workspace <tmp> --strict --output json`
- `uv run ralphite run --workspace <tmp> --yes --output json`
- `uv run --with pytest pytest apps/cli/tests/test_cli_output_contract.py apps/cli/tests/test_cli_ux_commands.py apps/cli/tests/test_cli_recover.py -q`

## Procedure

1. Execute each command 5 times.
2. Record median and p95 runtime.
3. Record max RSS from `/usr/bin/time -lp` output where available.
4. Save report JSON to `benchmarks/baseline/pre_pivot_cli.json` before the pivot.
5. Post-pivot comparisons must remain within 10% runtime and RSS regression.
