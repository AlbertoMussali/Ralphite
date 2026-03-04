from __future__ import annotations

from pathlib import Path

from ralphite_engine import LocalOrchestrator


def _plan_content() -> str:
    return """
version: 5
plan_id: orch
name: orch
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
constraints:
  max_parallel: 2
agents:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1-mini
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: openai
    model: gpt-4.1-mini
orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
tasks:
  - id: t1
    title: Plan
    completed: false
    routing:
      cell: seq_pre
  - id: t2
    title: Build A
    completed: false
    parallel_group: 1
    deps: [t1]
    routing:
      cell: par_core
  - id: t3
    title: Build B
    completed: false
    parallel_group: 1
    deps: [t1]
    routing:
      cell: par_core
  - id: t4
    title: Verify
    completed: false
    deps: [t2, t3]
    routing:
      cell: seq_post
outputs:
  required_artifacts: []
"""


def _conflict_plan_content() -> str:
    return """
version: 5
plan_id: recovery
name: recovery
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
agents:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1-mini
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: openai
    model: gpt-4.1-mini
orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
tasks:
  - id: t1
    title: Build
    completed: false
outputs:
  required_artifacts: []
"""


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


def test_v5_plan_executes_with_phase_events(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    run_id = orch.start_run(plan_content=_plan_content())
    events = list(orch.stream_events(run_id))
    names = [event["event"] for event in events]

    assert "PHASE_STARTED" in names
    assert "LANE_STARTED" in names
    assert "WORKER_STARTED" in names
    assert "WORKER_MERGED" in names
    assert "ORCH_DONE" in names
    assert "PHASE_DONE" in names
    assert "RUN_DONE" in names


def test_conflict_triggers_recovery_and_abort_mode(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    (tmp_path / ".ralphite" / "force_merge_conflict").write_text("phase-1", encoding="utf-8")
    run_id = orch.start_run(plan_content=_conflict_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "paused_recovery_required"
    assert any(evt.get("event") == "RECOVERY_REQUIRED" for evt in run.events)

    assert orch.set_recovery_mode(run_id, "abort_phase") is True
    assert orch.resume_from_checkpoint(run_id) is True
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    final = orch.get_run(run_id)
    assert final is not None
    assert final.status == "failed"
