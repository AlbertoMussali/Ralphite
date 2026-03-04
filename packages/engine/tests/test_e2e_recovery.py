from __future__ import annotations

from ralphite_engine import LocalOrchestrator


def _plan_content() -> str:
    return """
version: 4
plan_id: e2e_recovery
name: e2e_recovery
run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default
agents:
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
tasks:
  - id: t1
    title: Build
    completed: false
"""


def test_e2e_pause_recover_resume_success(tmp_path) -> None:
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
