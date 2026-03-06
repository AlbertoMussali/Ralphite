# Ralphite

Ralphite is a local-first, CLI-first multi-agent execution system for **v1 YAML plans**.

Last verified against commit: 70b0c1f

## Codex-First Happy Path

Requirements: Python 3.13+, `uv`, `git`, `rg`, and `codex` in `PATH`, and the workspace must already be inside a git worktree.

```bash
uv run ralphite init --workspace .
uv run ralphite quickstart --workspace . --yes --output table
uv run ralphite run --workspace . --yes --output table
```

What this should do:

- `init` creates `.ralphite/config.toml` and a starter v1 plan.
- `quickstart` runs `doctor`, verifies git/worktree readiness, bootstraps missing workspace state, shows the selected plan/backend/model/capability scope, and starts a first run.
- `run` executes the selected plan directly and returns a result with run id, next action, and artifact paths.
- Successful runs always leave behind a human-facing `final_report.md` with outcome, changed files, acceptance results, failures, and next steps.

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

If `doctor` reports `git-worktree` as failed, initialize the workspace first:

```bash
git init -b main
git add -A
git commit -m "initial workspace state"
```

## Canonical Starting Points

- Operators: start here in `README.md`, then follow [First Run](docs/workflows/first-run.md), then [Workflows Index](docs/workflows/index.md).
- Contributors: start with [CONTRIBUTING.md](CONTRIBUTING.md), then use the [Documentation Hub](docs/index.md).
- References (CLI/schema/contracts): use [docs/references/index.md](docs/references/index.md) after completing an operator or contributor start path.
- `USER_GUIDE.md` is a compact conceptual refresher, not the canonical entrypoint.

## Defaults and Compatibility

- Default backend: `codex`
- Optional backend: `cursor` (`agent` command)
- Default model: `gpt-5.3-codex`
- Default reasoning effort: `medium`
- Plan runtime: `version: 1` only
