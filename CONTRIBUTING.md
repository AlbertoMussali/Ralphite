# Contributing to Ralphite

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
