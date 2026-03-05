# Near-Term Roadmap

Owners: product, engine, cli
Last verified against commit: 70b0c1f

## Milestone 1: Stabilization (current)

Goals:

- Preserve deterministic strict checks behavior.
- Prevent docs/runtime contract drift.
- Keep backend contracts explicit and test-covered.

Exit criteria:

- `uv run ruff check .`, `uv run --with pytest pytest -q`, `uv run ralphite check --workspace . --full --output json`, and `uv run ralphite check --workspace . --strict --output json` all green.
- Docs checks and ADR governance checks green in CI.

## Milestone 2: Hardening Follow-Through

Goals:

- Expand cross-platform command contract coverage.
- Improve recovery observability and operator diagnostics.
- Tighten docs automation around schema/CLI drift.

Exit criteria:

- Zero unresolved P0 issues in `docs/exec-plans/tech-debt-tracker.md`.
- Recovery and failure taxonomy docs validated against current runtime behavior.
