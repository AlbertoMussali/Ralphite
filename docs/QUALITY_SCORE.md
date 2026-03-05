# Quality Score

Owners: release
Last verified against commit: 70b0c1f

## Current Signals

- lint: `uv run ruff check .`
- tests: `uv run --no-sync pytest -q`
- full check: `uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --full --output json`
- strict checks: `uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json`

## Scoring Model (Repo Internal)

- Gate pass/fail is binary release signal.
- Trend analysis should be tracked externally in release notes and CI history.
