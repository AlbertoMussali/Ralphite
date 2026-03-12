from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatusPresentation:
    code: str
    label: str
    severity: str
    next_action: str


@dataclass(frozen=True)
class EventPresentation:
    event: str
    title: str
    next_action: str


_RUN_STATUS: dict[str, StatusPresentation] = {
    "queued": StatusPresentation(
        "queued", "Queued", "info", "Start the run or wait for execution."
    ),
    "running": StatusPresentation(
        "running",
        "Running",
        "info",
        "Monitor timeline or pause if you need to intervene.",
    ),
    "paused": StatusPresentation(
        "paused", "Paused", "warn", "Resume the run when ready."
    ),
    "paused_recovery_required": StatusPresentation(
        "paused_recovery_required",
        "Needs Recovery",
        "error",
        "Open Recovery, run preflight, pick a mode, then resume.",
    ),
    "recovering": StatusPresentation(
        "recovering", "Recovering", "warn", "Wait for recovery checks to complete."
    ),
    "succeeded": StatusPresentation(
        "succeeded", "Succeeded", "info", "Review summary artifacts."
    ),
    "failed": StatusPresentation(
        "failed", "Failed", "error", "Inspect failure summary and rerun failed work."
    ),
    "cancelled": StatusPresentation(
        "cancelled", "Cancelled", "warn", "Replay from history if needed."
    ),
}

_EVENTS: dict[str, EventPresentation] = {
    "RUN_STARTED": EventPresentation(
        "RUN_STARTED", "Run Started", "Monitor phase progress."
    ),
    "RUN_DONE": EventPresentation(
        "RUN_DONE", "Run Finished", "Open Summary for results and next steps."
    ),
    "RUN_TIMEOUT": EventPresentation(
        "RUN_TIMEOUT",
        "Runtime Limit Reached",
        "Increase constraints.max_runtime_seconds or reduce scope.",
    ),
    "RUN_LIMIT_REACHED": EventPresentation(
        "RUN_LIMIT_REACHED",
        "Step Limit Reached",
        "Increase constraints.max_total_steps or simplify the plan.",
    ),
    "RECOVERY_REQUIRED": EventPresentation(
        "RECOVERY_REQUIRED",
        "Recovery Required",
        "Open Recovery screen and run preflight before resuming.",
    ),
    "RECOVERY_PREFLIGHT_FAILED": EventPresentation(
        "RECOVERY_PREFLIGHT_FAILED",
        "Recovery Preflight Failed",
        "Address blocking reasons and retry resume.",
    ),
}


def present_run_status(status: str) -> StatusPresentation:
    normalized = (status or "").strip()
    if normalized in _RUN_STATUS:
        return _RUN_STATUS[normalized]
    return StatusPresentation(
        normalized or "unknown",
        "Unknown",
        "warn",
        "Inspect timeline and run summary for details.",
    )


def present_event(event: str) -> EventPresentation:
    normalized = (event or "").strip()
    if normalized in _EVENTS:
        return _EVENTS[normalized]
    title = normalized.replace("_", " ").title() if normalized else "Event"
    return EventPresentation(
        normalized or "unknown", title, "Inspect event details for follow-up."
    )


def present_recovery_mode(mode: str | None) -> str:
    if mode == "agent_best_effort":
        return "Best Effort Agent"
    if mode == "abort_phase":
        return "Abort Phase"
    if mode == "manual":
        return "Manual"
    return "Not Selected"
