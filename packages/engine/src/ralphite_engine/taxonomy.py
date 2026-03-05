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
        command_hint="uv run ralphite replay <RUN_ID> --workspace . --no-tui --output table",
    ),
    "unknown_node_kind": FailureAdvice(
        code="unknown_node_kind",
        title="Unsupported Node",
        message="Plan contains an unsupported node kind for local engine execution.",
        next_action="Use v5 unified YAML plans with tasks plus orchestration sections.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "validation_error": FailureAdvice(
        code="validation_error",
        title="Plan Validation Failed",
        message="Plan schema or graph constraints failed validation.",
        next_action="Fix validation errors in the v5 plan YAML, then rerun.",
        command_hint="uv run ralphite validate --workspace . --json",
    ),
    "runtime_error": FailureAdvice(
        code="runtime_error",
        title="Runtime Error",
        message="Unexpected execution error occurred.",
        next_action="Inspect event timeline and rerun failed nodes.",
        command_hint="uv run ralphite history --workspace . --output table",
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
