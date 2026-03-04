from __future__ import annotations

from ralphite_tui.tui.screens.phase_timeline_screen import PhaseTimelineScreen


def _row(
    *,
    event: str,
    raw_event: str,
    phase: str,
    lane: str,
    level: str,
    message: str,
    task_id: str,
    next_action: str,
) -> dict[str, str]:
    return {
        "id": "1",
        "event": event,
        "raw_event": raw_event,
        "phase": phase,
        "lane": lane,
        "level": level,
        "message": message,
        "task_id": task_id,
        "next_action": next_action,
    }


def test_filtered_rows_supports_event_type_tokens(monkeypatch) -> None:
    screen = PhaseTimelineScreen()
    screen._event_rows = [
        _row(
            event="Task Failed",
            raw_event="TASK_FAILED",
            phase="phase_1",
            lane="worker",
            level="error",
            message="failed",
            task_id="t1",
            next_action="Inspect error",
        ),
        _row(
            event="Task Queued",
            raw_event="TASK_QUEUED",
            phase="phase_1",
            lane="worker",
            level="info",
            message="queued",
            task_id="t2",
            next_action="Wait",
        ),
    ]
    filters = {
        "filter-phase": "",
        "filter-lane": "",
        "filter-task": "",
        "filter-event-types": "failed",
    }
    monkeypatch.setattr(screen, "_filter_value", lambda key: filters[key])  # noqa: ARG005
    filtered = screen._filtered_rows()  # noqa: SLF001
    assert len(filtered) == 1
    assert filtered[0]["raw_event"] == "TASK_FAILED"


def test_filtered_rows_failure_next_action_toggle(monkeypatch) -> None:
    screen = PhaseTimelineScreen()
    screen._event_rows = [
        _row(
            event="Task Failed",
            raw_event="TASK_FAILED",
            phase="phase_1",
            lane="worker",
            level="error",
            message="failed",
            task_id="t1",
            next_action="Inspect error",
        ),
        _row(
            event="Task Failed",
            raw_event="TASK_FAILED",
            phase="phase_1",
            lane="worker",
            level="error",
            message="failed without guidance",
            task_id="t2",
            next_action="",
        ),
        _row(
            event="Task Running",
            raw_event="TASK_RUNNING",
            phase="phase_1",
            lane="worker",
            level="info",
            message="running",
            task_id="t3",
            next_action="Wait",
        ),
    ]
    filters = {
        "filter-phase": "",
        "filter-lane": "",
        "filter-task": "",
        "filter-event-types": "",
    }
    monkeypatch.setattr(screen, "_filter_value", lambda key: filters[key])  # noqa: ARG005
    screen._failures_with_next_action = True  # noqa: SLF001
    filtered = screen._filtered_rows()  # noqa: SLF001
    assert len(filtered) == 1
    assert filtered[0]["task_id"] == "t1"


def test_filtered_rows_combines_phase_lane_and_task_filters(monkeypatch) -> None:
    screen = PhaseTimelineScreen()
    screen._event_rows = [
        _row(
            event="Task Blocked",
            raw_event="TASK_BLOCKED",
            phase="phase_2",
            lane="worker-a",
            level="warn",
            message="task blocked waiting for t9",
            task_id="t8",
            next_action="Unblock dependency",
        ),
        _row(
            event="Task Blocked",
            raw_event="TASK_BLOCKED",
            phase="phase_1",
            lane="worker-b",
            level="warn",
            message="blocked waiting for t3",
            task_id="t4",
            next_action="Unblock dependency",
        ),
    ]
    filters = {
        "filter-phase": "phase_2",
        "filter-lane": "worker-a",
        "filter-task": "t8",
        "filter-event-types": "",
    }
    monkeypatch.setattr(screen, "_filter_value", lambda key: filters[key])  # noqa: ARG005
    filtered = screen._filtered_rows()  # noqa: SLF001
    assert len(filtered) == 1
    assert filtered[0]["phase"] == "phase_2"
    assert filtered[0]["lane"] == "worker-a"
