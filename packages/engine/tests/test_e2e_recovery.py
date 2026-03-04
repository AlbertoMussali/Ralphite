from __future__ import annotations

from pathlib import Path

from ralphite_engine import LocalOrchestrator


def _plan_content() -> str:
    return """
version: 3
plan_id: e2e_recovery
name: e2e_recovery
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


def test_e2e_pause_recover_resume_success(tmp_path: Path) -> None:
    (tmp_path / "RALPHEX_TASK.md").write_text(
        "\n".join(
            [
                "# Tasks",
                "- [ ] Build <!-- id:t1 phase:phase-1 lane:parallel agent_profile:worker_default -->",
            ]
        ),
        encoding="utf-8",
    )

    orch = LocalOrchestrator(tmp_path)
    marker = tmp_path / ".ralphite" / "force_merge_conflict"
    marker.write_text("phase-1", encoding="utf-8")

    run_id = orch.start_run(plan_content=_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    paused = orch.get_run(run_id)
    assert paused is not None
    assert paused.status == "paused_recovery_required"

    assert orch.set_recovery_mode(run_id, "manual") is True

    marker.unlink()
    preflight = orch.recovery_preflight(run_id)
    assert preflight.get("ok") is True

    assert orch.resume_from_checkpoint(run_id) is True
    assert orch.wait_for_run(run_id, timeout=8.0) is True

    final = orch.get_run(run_id)
    assert final is not None
    assert final.status in {"succeeded", "failed"}
    assert final.status != "paused_recovery_required"
    assert any(evt.get("event") == "RECOVERY_RESUMED" for evt in final.events)
