# Ralphite Documentation Hub

Owners: engine, cli, schemas, release
Last verified against commit: a7aaeed

Verification commands:

```bash
uv run ruff check .
uv run --no-sync pytest -q
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json
```

## Audience Navigation

- Operators: [docs/workflows/index.md](workflows/index.md)
- Contributors: [docs/design-docs/index.md](design-docs/index.md)
- Maintainers: [docs/architecture/index.md](architecture/index.md)
- Coding agents: [AGENTS.md](../AGENTS.md), [docs/references/index.md](references/index.md)

## Start Here

1. Product and quickstart: [README.md](../README.md)
2. Runtime model: [docs/architecture/runtime-execution.md](architecture/runtime-execution.md)
3. Gate and release policy: [docs/workflows/release-readiness.md](workflows/release-readiness.md)
4. Plan/spec references: [docs/references/plan-schema-reference.md](references/plan-schema-reference.md)
5. Canonical app examples: [docs/workflows/example-calendar-math-cli.md](workflows/example-calendar-math-cli.md), [docs/workflows/example-thermal-1d.md](workflows/example-thermal-1d.md)

## Source-of-Truth Policy

- Code + tests are canonical for behavior.
- Docs must match runtime contracts and schema defaults.
- Canonical tracked starter plans live under [examples/plans](../examples/plans/).
- Local `.ralphite/plans` files are workspace-local run state.
