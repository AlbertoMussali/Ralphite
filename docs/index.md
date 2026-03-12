# Ralphite Documentation Hub

Owners: engine, cli, schemas, release
Last verified against commit: 071697a

Verification commands:

```bash
uv run ruff check .
uv run --no-sync pytest -q
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json
```

## Audience Navigation

- Operators (canonical start): [README.md](../README.md) -> [docs/workflows/first-run.md](workflows/first-run.md) -> [docs/workflows/index.md](workflows/index.md)
- Contributors (canonical start): [CONTRIBUTING.md](../CONTRIBUTING.md) -> this hub (`docs/index.md`) -> [docs/design-docs/index.md](design-docs/index.md)
- Maintainers: [docs/architecture/index.md](architecture/index.md)
- Coding agents: [AGENTS.md](../AGENTS.md), [docs/references/index.md](references/index.md)

## Follow-On Reading

1. Product and quickstart: [README.md](../README.md)
2. Runtime model: [docs/architecture/runtime-execution.md](architecture/runtime-execution.md)
3. Gate and release policy: [docs/workflows/release-readiness.md](workflows/release-readiness.md)
4. Plan/spec references: [docs/references/plan-schema-reference.md](references/plan-schema-reference.md)
5. Starter templates: [starter_bugfix.yaml](../examples/plans/starter_bugfix.yaml), [starter_refactor.yaml](../examples/plans/starter_refactor.yaml), [starter_docs_update.yaml](../examples/plans/starter_docs_update.yaml), [starter_release_prep.yaml](../examples/plans/starter_release_prep.yaml)
6. Canonical app examples: [docs/workflows/example-calendar-math-cli.md](workflows/example-calendar-math-cli.md), [docs/workflows/example-thermal-1d.md](workflows/example-thermal-1d.md)

## Source-of-Truth Policy

- Code + tests are canonical for behavior.
- Docs must match runtime contracts and schema defaults.
- Canonical tracked starter templates live under [examples/plans](../examples/plans/).
- Local `.ralphite/plans` files are workspace-local run state.
