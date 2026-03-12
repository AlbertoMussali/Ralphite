# Failure Taxonomy

Owners: engine, release
Last verified against commit: a8f4411

Source file: `src/ralphite/engine/taxonomy.py`

## Backend Failures

- `backend_binary_missing`
- `backend_model_unsupported`
- `backend_auth_failed`
- `backend_nonzero`
- `backend_timeout`
- `backend_output_malformed`
- `backend_payload_missing`
- `backend_payload_malformed`
- `backend_out_of_worktree_claim`
- `backend_out_of_worktree_mutation`
- `backend_execution_error`
- `backend_worktree_missing`

## Acceptance/Runtime Failures

- `acceptance_command_timeout`
- `acceptance_artifact_out_of_bounds`
- `validation_error`
- `runtime_error`
- `task_writeback_failed`
- `task_writeback_commit_failed`

## Integration/Recovery Failures

- `base_integration_blocked_by_local_changes`
- `base_merge_conflict`
- `stale_recovery_state_present`
- `recovery_conflict_files_present`

`backend_out_of_worktree_claim` is informational/diagnostic and reflects a backend mention of an external path without confirmed mutation. `backend_out_of_worktree_mutation` is the fatal case and reflects confirmed local mutations outside the assigned write scope.

`base_integration_blocked_by_local_changes` is reserved for real content overlap. Ralphite now tolerates overlap on bookkeeping surfaces such as the active plan path, revision-only writeback file, and `.ralphite/` runtime files, and reports those separately as ignored overlap.

`base_merge_conflict` and `worker_merge_conflict` may include deterministic resolver metadata when Ralphite attempted narrow auto-resolution for additive export conflicts or simple markdown append-only conflicts before falling back to manual recovery.

Each failure maps to user-facing advice and command hints through `classify_failure()`.
