# Release Readiness Workflow

Owners: release, engine
Last verified against commit: 70b0c1f

## Deterministic Gates

```bash
uv run ruff check .
uv run --no-sync pytest -q
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --full --output json
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json
```

## Policy

- codex backend is required
- cursor backend is optional unless explicitly selected for target environments
- strict checks should not rely on runtime simulation fallback

## Real Backend Sign-Off

Run without skip env flags and capture command outputs.

## Sign-Off Artifact

Record in release notes:

- timestamp (local + UTC)
- commit SHA
- executed command list
- pass/fail outcomes
- any waived warnings + rationale
