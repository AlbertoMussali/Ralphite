from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureAdvice:
    code: str
    title: str
    message: str
    next_action: str
    command_hint: str


FAILURE_MAP: dict[str, FailureAdvice] = {
    "git_required": FailureAdvice(
        code="git_required",
        title="Git Worktree Required",
        message="Ralphite execution requires the workspace to be inside a git worktree.",
        next_action="Run inside an existing git repository or initialize one with `git init -b main` before executing Ralphite.",
        command_hint="git init -b main",
    ),
    "permission_denied": FailureAdvice(
        code="permission_denied",
        title="Permission Blocked",
        message="Run was blocked by current tool or MCP policy.",
        next_action="Approve required tools at run start or relax policy in .ralphite/config.toml.",
        command_hint="uv run ralphite doctor --workspace . --output table",
    ),
    "task_marker_failure": FailureAdvice(
        code="task_marker_failure",
        title="Task Triggered Failure",
        message="Task content explicitly triggered failure simulation marker.",
        next_action="Remove '[fail]' markers and rerun failed tasks.",
        command_hint="uv run ralphite replay <RUN_ID> --workspace . --output table",
    ),
    "backend_binary_missing": FailureAdvice(
        code="backend_binary_missing",
        title="Backend CLI Missing",
        message="Required headless backend command was not found on PATH.",
        next_action="Install/configure the selected backend CLI (codex or agent) and rerun.",
        command_hint="uv run ralphite doctor --workspace . --output table",
    ),
    "backend_model_unsupported": FailureAdvice(
        code="backend_model_unsupported",
        title="Backend Model Unsupported",
        message="The selected backend does not support the configured model id.",
        next_action="Switch model to a backend-supported id or update backend account access.",
        command_hint="uv run ralphite run --workspace . --output table --model gpt-5.3-codex",
    ),
    "backend_auth_failed": FailureAdvice(
        code="backend_auth_failed",
        title="Backend Authentication Failed",
        message="Headless backend rejected authentication or lacks a valid session.",
        next_action="Log in to the backend CLI and retry.",
        command_hint="codex login",
    ),
    "backend_nonzero": FailureAdvice(
        code="backend_nonzero",
        title="Backend Command Failed",
        message="Headless backend exited with a non-zero status.",
        next_action="Inspect backend stderr details and retry with corrected prompt/config.",
        command_hint="uv run ralphite history --workspace . --output table",
    ),
    "backend_timeout": FailureAdvice(
        code="backend_timeout",
        title="Backend Timed Out",
        message="Headless backend exceeded runtime timeout.",
        next_action="Increase run constraints timeout or simplify the task scope.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "backend_output_malformed": FailureAdvice(
        code="backend_output_malformed",
        title="Malformed Backend Output",
        message="Headless backend output could not be parsed.",
        next_action="Verify backend CLI flags/output format and rerun.",
        command_hint="uv run ralphite doctor --workspace . --output table",
    ),
    "backend_payload_missing": FailureAdvice(
        code="backend_payload_missing",
        title="Backend Payload Missing",
        message="Headless backend exited cleanly but did not emit a final completion payload.",
        next_action="Inspect salvaged work and backend stdout/stderr, then retry or promote retained output.",
        command_hint="uv run ralphite salvage --workspace . --run-id <RUN_ID> --output table",
    ),
    "backend_payload_malformed": FailureAdvice(
        code="backend_payload_malformed",
        title="Backend Payload Malformed",
        message="Headless backend emitted a completion payload Ralphite could not parse reliably.",
        next_action="Inspect salvaged work and backend payload diagnostics, then retry with the supported output contract.",
        command_hint="uv run ralphite salvage --workspace . --run-id <RUN_ID> --output table",
    ),
    "backend_out_of_worktree_claim": FailureAdvice(
        code="backend_out_of_worktree_claim",
        title="Out-of-Worktree Claim Recorded",
        message="Backend output mentioned paths outside the assigned worktree, but no out-of-scope mutation was confirmed.",
        next_action="Inspect diagnostics if needed; only observed filesystem mutations are treated as fatal.",
        command_hint="uv run ralphite replay <RUN_ID> --workspace . --output table",
    ),
    "backend_out_of_worktree_mutation": FailureAdvice(
        code="backend_out_of_worktree_mutation",
        title="Out-of-Scope Mutation Rejected",
        message="Observed file mutations exceeded the assigned write scope.",
        next_action="Inspect retained work and tighten task write policy or worker behavior before retrying.",
        command_hint="uv run ralphite salvage --workspace . --run-id <RUN_ID> --output table",
    ),
    "backend_execution_error": FailureAdvice(
        code="backend_execution_error",
        title="Backend Execution Error",
        message="Unexpected error while invoking headless backend.",
        next_action="Inspect local runtime environment and retry.",
        command_hint="uv run ralphite doctor --workspace . --output table",
    ),
    "backend_worktree_missing": FailureAdvice(
        code="backend_worktree_missing",
        title="Worktree Missing",
        message="Assigned worktree path was missing when execution started.",
        next_action="Recreate run/worktree by replaying failed run.",
        command_hint="uv run ralphite replay <RUN_ID> --workspace . --output table",
    ),
    "defaults.placeholder_invalid": FailureAdvice(
        code="defaults.placeholder_invalid",
        title="Prompt Template Invalid",
        message="Agent defaults prompt template used an invalid placeholder token.",
        next_action="Fix invalid placeholders in system_prompt/prompt_template and rerun validate.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "unknown_node_kind": FailureAdvice(
        code="unknown_node_kind",
        title="Unsupported Node",
        message="Plan contains an unsupported node kind for local engine execution.",
        next_action="Use v1 unified YAML plans with tasks plus orchestration sections.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "validation_error": FailureAdvice(
        code="validation_error",
        title="Plan Validation Failed",
        message="Plan schema or graph constraints failed validation.",
        next_action="Fix validation errors in the v1 plan YAML, then rerun.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "runtime_error": FailureAdvice(
        code="runtime_error",
        title="Runtime Error",
        message="Unexpected execution error occurred.",
        next_action="Inspect event timeline and rerun failed nodes.",
        command_hint="uv run ralphite history --workspace . --output table",
    ),
    "base_integration_blocked_by_local_changes": FailureAdvice(
        code="base_integration_blocked_by_local_changes",
        title="Base Integration Blocked",
        message="Merge to the primary workspace was blocked by overlapping local changes.",
        next_action="Commit, stash, or manually reconcile the overlapping workspace edits before resuming recovery.",
        command_hint="uv run ralphite recover --workspace . --preflight-only --output table",
    ),
    "stale_recovery_state_present": FailureAdvice(
        code="stale_recovery_state_present",
        title="Stale Recovery State Present",
        message="A previous recoverable run or managed git artifact is still active in this workspace.",
        next_action="Resolve the existing run first, or clean the stale managed artifacts before starting a new run.",
        command_hint="uv run ralphite history --workspace . --output table",
    ),
    "recovery_conflict_files_present": FailureAdvice(
        code="recovery_conflict_files_present",
        title="Recovery Conflicts Still Present",
        message="Recovery cannot continue because unresolved conflict files remain in the recovery worktree.",
        next_action="Resolve the listed conflicts, stage the result, and resume recovery.",
        command_hint="uv run ralphite recover --workspace . --output table",
    ),
    "acceptance_command_timeout": FailureAdvice(
        code="acceptance_command_timeout",
        title="Acceptance Timeout",
        message="A task acceptance command exceeded the configured timeout.",
        next_action="Increase constraints.acceptance_timeout_seconds or fix the hanging command.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "acceptance_artifact_out_of_bounds": FailureAdvice(
        code="acceptance_artifact_out_of_bounds",
        title="Artifact Path Rejected",
        message="Acceptance artifact glob resolved outside the task worktree.",
        next_action="Use a relative path_glob that stays within the worktree.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "git_add_failed": FailureAdvice(
        code="git_add_failed",
        title="Plan Write-Back Blocked",
        message="Git could not stage plan updates, usually because the plan path is ignored.",
        next_action="Use a tracked plan path or set task_writeback_mode to 'revision_only' or 'disabled'.",
        command_hint="uv run ralphite doctor --workspace . --output table",
    ),
    "task_writeback_failed": FailureAdvice(
        code="task_writeback_failed",
        title="Task Write-Back Failed",
        message="Ralphite could not update completed task flags in the plan.",
        next_action="Validate plan YAML structure and rerun, or disable write-back in config.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "task_writeback_commit_failed": FailureAdvice(
        code="task_writeback_commit_failed",
        title="Write-Back Commit Failed",
        message="Task write-back succeeded, but commit creation failed.",
        next_action="Inspect git status/config and rerun with task_writeback_mode='revision_only' if needed.",
        command_hint="uv run ralphite doctor --workspace . --output table",
    ),
}


def classify_failure(reason: str) -> FailureAdvice:
    for key, advice in FAILURE_MAP.items():
        if reason.startswith(key):
            return advice
    return FailureAdvice(
        code="unknown",
        title="Unknown Failure",
        message="An unknown error occurred during execution.",
        next_action="Use `ralphite history --query failed` and inspect logs/events for details.",
        command_hint="uv run ralphite history --workspace . --query failed --output table",
    )
