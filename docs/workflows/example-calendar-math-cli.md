# Example: Calendar Math CLI ([easy])

Owners: cli, engine
Last verified against commit: a7aaeed

## Goal

Canonical v1 plan for a small pure-Python interactive CLI that performs calendar date arithmetic.

## Why `general_sps`

This app maps cleanly onto `seq_pre -> par_core -> seq_post`:

- `seq_pre`: lock requirements and edge-case rules first.
- `par_core`: implement date math and interactive shell in parallel.
- `seq_post`: integrate, verify, and publish docs.

This showcases sequential + parallel cells without introducing lane complexity.

## Task Routing Matrix

| Task ID | Purpose | Routing |
|---|---|---|
| `task_requirements` | Define UX + calendar rules | `cell: seq_pre` |
| `task_date_math_core` | Build core date arithmetic | `cell: par_core` |
| `task_interactive_cli` | Build interactive prompt loop | `cell: par_core` |
| `task_integration_polish` | Integrate modules and harden UX | `cell: seq_post` |
| `task_tests_and_readme` | Final tests + usage docs | `cell: seq_post` |

## Canonical Plan

- `examples/plans/example_easy_calendar_math_cli.yaml`

## Execute

Validate the plan:

```bash
uv run ralphite validate --workspace . --plan examples/plans/example_easy_calendar_math_cli.yaml --json
```

Run with real backend:

```bash
uv run ralphite run --workspace . --plan examples/plans/example_easy_calendar_math_cli.yaml --yes --output stream
```

Run with deterministic local simulation:

```bash
RALPHITE_DEV_SIMULATED_EXECUTION=1 \
RALPHITE_SKIP_BACKEND_CMD_CHECKS=1 \
uv run ralphite run --workspace . --plan examples/plans/example_easy_calendar_math_cli.yaml --yes --output stream
```

## Expected Outputs

- `final_report` (markdown human summary with outcome, changed files, acceptance results, failures, and next steps)
- `machine_bundle` (json)
- task-level artifacts under `src/calendar_math/`, `tests/`, and `docs/calendar_math/`
