from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureAdvice:
    code: str
    title: str
    message: str
    next_action: str


FAILURE_MAP: dict[str, FailureAdvice] = {
    "permission_denied": FailureAdvice(
        code="permission_denied",
        title="Permission Blocked",
        message="Run was blocked by current tool or MCP policy.",
        next_action="Approve required tools at run start or relax policy in .ralphite/config.toml.",
    ),
    "task_marker_failure": FailureAdvice(
        code="task_marker_failure",
        title="Task Triggered Failure",
        message="Task content explicitly triggered failure simulation marker.",
        next_action="Remove '[fail]' markers and rerun failed tasks.",
    ),
    "unknown_node_kind": FailureAdvice(
        code="unknown_node_kind",
        title="Unsupported Node",
        message="Plan contains an unsupported node kind for local engine execution.",
        next_action="Use v4 unified YAML plans with task, run, and agent sections.",
    ),
    "validation_error": FailureAdvice(
        code="validation_error",
        title="Plan Validation Failed",
        message="Plan schema or graph constraints failed validation.",
        next_action="Fix validation errors in the v4 plan YAML, then rerun.",
    ),
    "runtime_error": FailureAdvice(
        code="runtime_error",
        title="Runtime Error",
        message="Unexpected execution error occurred.",
        next_action="Inspect event timeline and rerun failed nodes.",
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
    )
