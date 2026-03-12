# Persistence and State

Owners: engine
Last verified against commit: 071697a

Source files:

- `src/ralphite/engine/run_store.py`
- `src/ralphite/engine/recovery.py`
- `src/ralphite/engine/orchestrator.py`
- `src/ralphite/engine/git_worktree.py`
- `src/ralphite/engine/git_runtime_state.py`
- `src/ralphite/engine/git_runtime_prepare.py`
- `src/ralphite/engine/git_runtime_cleanup.py`
- `src/ralphite/engine/runtime_bootstrap.py`
- `src/ralphite/engine/runtime_recovery_manager.py`
- `src/ralphite/engine/runtime_artifacts.py`
- `src/ralphite/engine/runtime_events.py`

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

- `final_report.md` (human summary artifact with outcome, changed files, acceptance results, failures, next steps, and supporting paths)
- `run_metrics.json`
- `machine_bundle.json`
- `salvage_bundle.json`

## Recovery-Derived State

- Git/worktree state is the authority for worker mutation truth during reconcile, recover, resume, and salvage promotion.
- `reconcile` refreshes `run.metadata.git_state`, `run.metadata.retained_work`, and `run.metadata.git_reconciliation` from current git/worktree truth.
- `reconcile --apply` also repairs cached run/checkpoint state by marking git-merged nodes succeeded, requeueing recoverable committed/retained workers, and clearing stale derived markers.
- Salvage promotion updates node result state in persisted run state and rewrites artifacts so operators can see the promoted outcome without manual JSON edits.
- Runs now record `reconciled_at`, `derived_from_git`, and `reconciliation_issues` in metadata when state is re-derived from repository truth.

The public state contract is unchanged. The implementation now splits bootstrap, recovery, artifacts, and event persistence behind the `LocalOrchestrator` facade instead of concentrating that logic in one module.

## Worker Evidence and Salvage

- Worker completion payloads are advisory; Ralphite confirms write scope from local worktree evidence before classifying success/failure.
- Acceptance commands are executed as direct argv invocations rather than shell-expanded command strings, so wildcard expansion and shell syntax are not part of the runtime contract.
- Retained work records now include salvage class plus captured stdout/stderr/backend diagnostics so malformed or partial worker completions remain inspectable and promotable.

## Task Write Policy

- Tasks may declare `write_policy.allowed_write_roots`, `write_policy.forbidden_write_roots`, `write_policy.allow_plan_edits`, and `write_policy.allow_root_writes`.
- If `allowed_write_roots` is omitted, Ralphite derives a conservative allowlist from declared acceptance artifact roots.
- Plan file edits are forbidden unless `allow_plan_edits: true`.

## Writeback Modes

`[run].task_writeback_mode`:

- `revision_only` (default)
- `in_place`
- `disabled`
