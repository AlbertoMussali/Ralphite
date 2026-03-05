# First Run Workflow

Owners: cli
Last verified against commit: 70b0c1f

## Deterministic Path

```bash
uv run ralphite init --workspace .
uv run ralphite quickstart --workspace . --yes --output stream
uv run ralphite run --workspace . --yes --output stream
```

Optional backend override:

```bash
uv run ralphite run --workspace . --backend codex --model gpt-5.3-codex --reasoning-effort medium --yes --output json
```

## Expected Outcome

- run reaches `succeeded` or provides typed failure reason + next actions in CLI output envelope.
