# Failure Taxonomy

Owners: engine, release
Last verified against commit: 70b0c1f

Source file: `packages/engine/src/ralphite_engine/taxonomy.py`

## Backend Failures

- `backend_binary_missing`
- `backend_model_unsupported`
- `backend_auth_failed`
- `backend_nonzero`
- `backend_timeout`
- `backend_output_malformed`
- `backend_out_of_worktree_claim`
- `backend_execution_error`
- `backend_worktree_missing`

## Acceptance/Runtime Failures

- `acceptance_command_timeout`
- `acceptance_artifact_out_of_bounds`
- `validation_error`
- `runtime_error`
- `task_writeback_failed`
- `task_writeback_commit_failed`

Each failure maps to user-facing advice and command hints through `classify_failure()`.
