# Ralphite Documentation Hub

Owners: engine, tui, schemas, release
Last verified against commit: 70b0c1f

Verification commands:

```bash
uv run ruff check .
uv run --with pytest pytest -q
uv run ralphite check --workspace . --release-gate --output json
```

## Audience Navigation

- Operators: [docs/workflows/index.md](workflows/index.md)
- Contributors: [docs/design-docs/index.md](design-docs/index.md)
- Maintainers: [docs/architecture/index.md](architecture/index.md)
- Coding agents: [AGENTS.md](../AGENTS.md), [docs/references/index.md](references/index.md)

## Start Here

1. Product and quickstart: [README.md](../README.md)
2. Runtime model: [docs/architecture/runtime-execution.md](architecture/runtime-execution.md)
3. Gate and release policy: [docs/workflows/beta-release.md](workflows/beta-release.md)
4. Plan/spec references: [docs/references/plan-v5-schema-reference.md](references/plan-v5-schema-reference.md)

## Source-of-Truth Policy

- Code + tests are canonical for behavior.
- Docs must match runtime contracts and schema defaults.
- Canonical tracked starter plans live under [examples/plans](../examples/plans/).
- Local `.ralphite/plans` files are workspace-local run state.
