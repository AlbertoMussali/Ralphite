# Ralphite

Ralphite is now a **terminal-first** local orchestration platform for solo builders.

## Current Product Shape (Hard Pivot)

- `apps/tui`: `ralphite` CLI + Textual dashboard (primary UX)
- `packages/engine`: in-process local orchestrator runtime
- `packages/schemas`: shared plan/event schemas
- `apps/api`, `apps/runner`, `apps/web`: legacy compatibility surfaces (deprecated)

## Quick Start (TUI-First)

### Requirements

- Python 3.12+
- Node 20+
- pnpm 9+
- `uv`

### Install everything

```bash
uv sync --all-packages
pnpm install
```

### Initialize workspace

```bash
uv run ralphite init --workspace .
```

### Run doctor checks

```bash
uv run ralphite doctor --workspace .
```

### Start from latest plan

```bash
uv run ralphite run --workspace .
```

### Goal-to-plan quickstart

```bash
uv run ralphite run --workspace . --goal "Refactor build pipeline and ship changelog"
```

### Dashboard

```bash
uv run ralphite tui --workspace .
```

### History, replay, migration

```bash
uv run ralphite history --workspace .
uv run ralphite replay <RUN_ID> --workspace .
uv run ralphite migrate --workspace .
```

### Quality gate

```bash
uv run ralphite check --workspace . --full
```

## Workspace Files

Ralphite uses local workspace state under `.ralphite/`:

- `.ralphite/config.toml` local profile/policy
- `.ralphite/plans/` plan files
- `.ralphite/runs/history.json` run history
- `.ralphite/artifacts/<run_id>/` final artifacts
- `.ralphite/drafts/` autosaved drafts

## Legacy Services (Deprecated)

The legacy web/API/runner stack is still present for transition purposes:

- Web: `pnpm --filter @ralphite/web dev`
- API: `PYTHONPATH="apps/api/src:packages/schemas/python/src" uv run python -m uvicorn ralphite_api.main:app --reload --port 8000`
- Runner: `PYTHONPATH="apps/runner/src:packages/schemas/python/src" uv run python -m ralphite_runner.main --api-base http://localhost:8000 --workspace-root /absolute/path/to/project`

These surfaces will be sunset after TUI parity and migration period.
