# Recovery Workflow

Owners: engine, cli
Last verified against commit: 70b0c1f

## Preflight

```bash
uv run ralphite recover --workspace . --preflight-only --output json
```

## Manual Recovery

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode manual --output json
```

## Agent Best Effort

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode agent_best_effort --prompt "resolve conflicts" --resume --output json
```

## Exit Code Contract

- `0` success
- `10` no recoverable run
- `11` unrecoverable/not found
- `12` invalid input/mode
- `13` preflight failed
- `14` pending
- `15` terminal failure
- `16` internal error
