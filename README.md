# Ralphite

Ralphite is a local-first, CLI-first multi-agent execution system for **v1 YAML plans**.

Last verified against commit: 70b0c1f

## Codex-First Happy Path

Requirements: Python 3.13+, `uv`, `git`, `rg`, and `codex` in `PATH`.

```bash
uv run ralphite init --workspace .
uv run ralphite quickstart --workspace . --yes --output table
uv run ralphite run --workspace . --yes --output table
```

What this should do:

- `init` creates `.ralphite/config.toml` and a starter v1 plan.
- `quickstart` runs `doctor`, bootstraps missing workspace state, shows the selected plan/backend/model/capability scope, and starts a first run.
- `run` executes the selected plan directly and returns a result with run id, next action, and artifact paths.

## Starter Templates

Tracked starter templates live under [examples/plans](examples/plans/) and are meant to be copied, renamed, and customized:

- [starter_bugfix.yaml](examples/plans/starter_bugfix.yaml): reproduce a reported issue, ship a scoped fix, and run a regression/review loop.
- [starter_refactor.yaml](examples/plans/starter_refactor.yaml): capture invariants, perform a safe internal cleanup, and verify behavior parity.
- [starter_docs_update.yaml](examples/plans/starter_docs_update.yaml): update docs and examples from code/test truth, then verify links and commands.
- [starter_release_prep.yaml](examples/plans/starter_release_prep.yaml): coordinate release scope, deterministic gates, cold-start checks, and sign-off.

If the happy path fails:

- `uv run ralphite doctor --workspace . --output table`
- `uv run ralphite history --workspace . --output table`
- `uv run ralphite recover --workspace . --output table`

## What To Read Next

- First-run operator guide: [docs/workflows/first-run.md](docs/workflows/first-run.md)
- Doc hub: [docs/index.md](docs/index.md)
- User workflows: [docs/workflows/index.md](docs/workflows/index.md)
- Architecture detail: [docs/architecture/index.md](docs/architecture/index.md)
- CLI and schema references: [docs/references/index.md](docs/references/index.md)
- Release readiness runbook: [docs/workflows/release-readiness.md](docs/workflows/release-readiness.md)

## Defaults and Compatibility

- Default backend: `codex`
- Optional backend: `cursor` (`agent` command)
- Default model: `gpt-5.3-codex`
- Default reasoning effort: `medium`
- Plan runtime: `version: 1` only
