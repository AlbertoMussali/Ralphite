from __future__ import annotations

from pathlib import Path

from ralphite_engine import LocalOrchestrator


def test_goal_plan_run_succeeds(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    plan_path = orch.goal_to_plan("Create a simple test artifact")

    run_id = orch.start_run(plan_ref=str(plan_path))
    events = list(orch.stream_events(run_id))

    assert any(event["event"] == "RUN_DONE" for event in events)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status in {"succeeded", "failed"}
    assert any(item["id"] == "final_report" for item in run.artifacts)


def test_cancel_run(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    plan_path = orch.goal_to_plan("Longish task to test cancel")
    run_id = orch.start_run(plan_ref=str(plan_path))
    assert orch.cancel_run(run_id) is True

    events = list(orch.stream_events(run_id))
    run = orch.get_run(run_id)

    assert run is not None
    assert run.status in {"cancelled", "failed", "succeeded"}
    assert any(evt["event"] in {"RUN_CANCEL_REQUESTED", "RUN_DONE"} for evt in events)
