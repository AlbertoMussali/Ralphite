# Beta Release Checklist

This checklist is the canonical pre-release runbook for beta sign-off.

## Deterministic Commands (must pass)

Run from repo root:

```bash
uv run ruff check .
uv run --with pytest pytest -q
uv run ralphite check --workspace . --full --output json
uv run ralphite check --workspace . --release-gate --output json
uv run ralphite check --workspace . --beta-gate --output json
```

Pass criteria:

- every command exits `0`
- no failed suites in JSON payloads
- no blocking doctor checks for beta gate

## Real Backend Sign-Off (manual pre-release)

Run without backend-skip env overrides:

```bash
uv run ralphite doctor --workspace . --output table
uv run ralphite check --workspace . --beta-gate --output json
```

If cursor backend is selected in config/CLI for target beta users, also verify:

```bash
uv run ralphite run --workspace . --backend cursor --model gpt-5.3-codex --reasoning-effort medium --no-tui --yes --output json
```

## Sign-Off Artifact

Record the following in release notes for each beta candidate:

- date/time (local + UTC)
- commit SHA
- executed commands
- pass/fail outcome for each command
- any waived warnings with rationale
