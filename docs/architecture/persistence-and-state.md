# Persistence and State

Owners: engine
Last verified against commit: 70b0c1f

Source files:

- `packages/engine/src/ralphite_engine/run_store.py`
- `packages/engine/src/ralphite_engine/recovery.py`
- `packages/engine/src/ralphite_engine/orchestrator.py`

## Workspace Data Layout

- `.ralphite/config.toml`
- `.ralphite/plans/*.yaml` (workspace-local)
- `.ralphite/runs/<run_id>/run_state.json`
- `.ralphite/runs/<run_id>/checkpoint.json`
- `.ralphite/runs/<run_id>/event_log.ndjson`
- `.ralphite/artifacts/<run_id>/`
- `.ralphite/worktrees/<run_id>/`

## Artifact Outputs

Typical generated artifacts:

- `final_report.md`
- `run_metrics.json`
- `machine_bundle.json`

## Writeback Modes

`[run].task_writeback_mode`:

- `revision_only` (default)
- `in_place`
- `disabled`
