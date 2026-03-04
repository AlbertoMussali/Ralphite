# Ralphite Architecture

## Primary Architecture (TUI-First)

- `apps/tui`: terminal UX (`ralphite` CLI + Textual dashboard)
- `packages/engine`: local in-process orchestrator
- `packages/schemas`: shared plan/event contracts

## Local Runtime Flow

1. `ralphite init` creates `.ralphite` layout and seeds starter plan if needed.
2. `ralphite doctor` validates environment, config, and plan health.
3. `ralphite run` resolves a plan (or generates from `--goal`) and starts a local run.
4. Engine validates plan, builds node runtime state, and executes locally.
5. Dashboard streams ordered run events and exposes pause/resume/cancel controls.
6. Engine writes artifacts and run history under `.ralphite/`.
7. `ralphite history` and `ralphite replay` support iteration and reruns.

## Engine Interface

- `start_run(plan_ref|plan_content)`
- `stream_events(run_id)`
- `pause_run(run_id)`
- `resume_run(run_id)`
- `cancel_run(run_id)`
- `rerun_failed(run_id)`

## Event Shape

Every event follows:

- `id`, `ts`, `run_id`, `group`, `task_id`, `stage`, `event`, `level`, `message`, `meta`

Primary events:

- `RUN_STARTED`, `NODE_STARTED`, `NODE_RESULT`
- `GATE_PASS`, `GATE_RETRY`, `GATE_FAIL`
- `RUN_SUMMARY`, `RUN_DONE`

## Legacy Surfaces (Deprecated)

- `apps/web` (React control plane)
- `apps/api` (FastAPI orchestration API)
- `apps/runner` (daemon)

These remain for migration compatibility only.
