# Contributing to Ralphite

Last verified against commit: 071697a

Thank you for your interest in contributing to Ralphite!

## Development Environment

We use `uv` for dependency management.

```bash
# Clone and sync
git clone <repo-url>
cd Ralphite
uv sync
```

## Verification Commands

Always run these commands before submitting a PR:

```bash
# Linting
uv run ruff check .

# Fast tests
uv run --no-sync pytest -q

# Strict workplace check
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json
```

## Documentation Source-of-Truth

Ralphite follows a strict documentation policy. If you change behavior, you MUST update the corresponding documentation:

- **CLI flags/outputs**: Update `docs/references/cli-contracts.md`.
- **Schema changes**: Update `src/ralphite/schemas/json/plan-spec.schema.json`.
- **Architecture changes**: Add an Architecture Decision Record (ADR) in `docs/decisions/`.

Refer to `AGENTS.md` for a full list of source-of-truth files.

Contract update matrix:

- Runtime/authority or recovery changes: update `docs/architecture/runtime-execution.md`, `docs/architecture/persistence-and-state.md`, `docs/architecture/security-and-trust-model.md`, and the relevant ADR.
- CLI behavior changes: update `docs/references/cli-contracts.md` and any impacted workflow docs under `docs/workflows/`.
- Schema changes: update `docs/references/plan-schema-reference.md` and `docs/generated/schema-summary.md`.
- Backend command builder changes: update `docs/generated/command-contracts.md`.

Authority/recovery changes should align with [ADR-0008](docs/decisions/ADR-0008-git-authority-and-derived-recovery.md).

## Contributor Docs Path

Use this order to avoid jumping between competing entrypoints:

1. [CONTRIBUTING.md](CONTRIBUTING.md) (this file) for local setup and required verification commands.
2. [docs/index.md](docs/index.md) for audience navigation and canonical doc map.
3. [docs/design-docs/index.md](docs/design-docs/index.md) for contributor-facing technical context.
4. [docs/references/index.md](docs/references/index.md) only when you need exact CLI/schema/test contracts.
