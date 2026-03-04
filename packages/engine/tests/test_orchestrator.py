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


def test_v3_plan_executes_with_phase_events(tmp_path: Path) -> None:
    (tmp_path / "RALPHEX_TASK.md").write_text(
        "\n".join(
            [
                "# Tasks",
                "- [ ] Plan <!-- id:t1 phase:phase-1 lane:seq_pre agent_profile:worker_default -->",
                "- [ ] Build A <!-- id:t2 phase:phase-1 lane:parallel deps:t1 agent_profile:worker_default -->",
                "- [ ] Build B <!-- id:t3 phase:phase-1 lane:parallel deps:t1 agent_profile:worker_default -->",
                "- [ ] Verify <!-- id:t4 phase:phase-1 lane:seq_post deps:t2,t3 agent_profile:worker_default -->",
            ]
        ),
        encoding="utf-8",
    )
    plan_content = """
version: 3
plan_id: v3_orch
name: v3_orch
task_source:
  kind: markdown_checklist
  path: RALPHEX_TASK.md
  parser_version: 3
agent_profiles:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1-mini
    tools_allow: [tool:*]
  - id: orchestrator_pre_default
    role: orchestrator_pre
    provider: openai
    model: gpt-4.1-mini
  - id: orchestrator_post_default
    role: orchestrator_post
    provider: openai
    model: gpt-4.1-mini
execution_structure:
  phases:
    - id: phase-1
      pre_orchestrator:
        enabled: false
        agent_profile_id: orchestrator_pre_default
      post_orchestrator:
        enabled: true
        agent_profile_id: orchestrator_post_default
constraints:
  max_parallel: 2
"""
    orch = LocalOrchestrator(tmp_path)
    run_id = orch.start_run(plan_content=plan_content)
    events = list(orch.stream_events(run_id))
    names = [event["event"] for event in events]

    assert "PHASE_STARTED" in names
    assert "LANE_STARTED" in names
    assert "WORKER_STARTED" in names
    assert "WORKER_MERGED" in names
    assert "ORCH_POST_DONE" in names
    assert "PHASE_DONE" in names
    assert "RUN_DONE" in names


def test_conflict_triggers_recovery_and_abort_mode(tmp_path: Path) -> None:
    (tmp_path / "RALPHEX_TASK.md").write_text(
        "\n".join(
            [
                "# Tasks",
                "- [ ] Build <!-- id:t1 phase:phase-1 lane:parallel agent_profile:worker_default -->",
            ]
        ),
        encoding="utf-8",
    )
    plan_content = """
version: 3
plan_id: v3_recovery
name: v3_recovery
task_source:
  kind: markdown_checklist
  path: RALPHEX_TASK.md
  parser_version: 3
agent_profiles:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1-mini
    tools_allow: [tool:*]
  - id: orchestrator_pre_default
    role: orchestrator_pre
    provider: openai
    model: gpt-4.1-mini
  - id: orchestrator_post_default
    role: orchestrator_post
    provider: openai
    model: gpt-4.1-mini
execution_structure:
  phases:
    - id: phase-1
      pre_orchestrator:
        enabled: false
        agent_profile_id: orchestrator_pre_default
      post_orchestrator:
        enabled: true
        agent_profile_id: orchestrator_post_default
"""
    orch = LocalOrchestrator(tmp_path)
    (tmp_path / ".ralphite" / "force_merge_conflict").write_text("phase-1", encoding="utf-8")
    run_id = orch.start_run(plan_content=plan_content)
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
