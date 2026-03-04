# Ralphite Architecture

## Services

- `apps/web`: React + Vite control plane UI
- `apps/api`: FastAPI API + orchestration state machine
- `apps/runner`: local daemon that executes claimed nodes using local tools/MCPs
- `packages/schemas`: shared contracts for plan/event payloads

## Runtime flow

1. Runner registers and heartbeats with capability payload.
2. User authenticates in web and creates project.
3. User connects workspace root. API binds project to matching runner.
4. Runner heartbeats discovered plans from `<root>/.ralphite/plans`.
5. User selects or uploads plan, validates, configures permissions, starts run.
6. API creates run + node records + permission snapshot.
7. Runner claims next ready node, executes, and posts completion/events.
8. API evaluates gates/loops, schedules more nodes, and emits SSE run events.
9. API finalizes run and stores structured artifacts.

## Event shape

Every event follows:

- `ts`, `run_id`, `group`, `task_id`, `stage`, `event`, `level`, `message`, `meta`

Primary events:

- `RUN_PLAN_READY`, `RUN_STARTED`, `NODE_STARTED`, `NODE_HEARTBEAT`, `NODE_RESULT`
- `GATE_PASS`, `GATE_RETRY`, `GATE_FAIL`
- `RUN_SUMMARY`, `RUN_DONE`
