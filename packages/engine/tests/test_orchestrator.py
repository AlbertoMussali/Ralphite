from __future__ import annotations

from pathlib import Path
from types import MethodType

import pytest
from ralphite_engine import LocalOrchestrator
import yaml


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


def _single_task_plan(
    *,
    acceptance_commands: list[str] | None = None,
    acceptance_artifacts: list[dict[str, str]] | None = None,
    max_retries_per_node: int = 0,
    acceptance_timeout_seconds: int = 120,
) -> str:
    plan = {
        "version": 5,
        "plan_id": "acceptance",
        "name": "acceptance",
        "materials": {
            "autodiscover": {"enabled": False, "path": ".", "include_globs": []},
            "includes": [],
            "uploads": [],
        },
        "constraints": {
            "max_retries_per_node": max_retries_per_node,
            "acceptance_timeout_seconds": acceptance_timeout_seconds,
        },
        "agents": [
            {"id": "worker_default", "role": "worker", "provider": "openai", "model": "gpt-4.1-mini"},
            {"id": "orchestrator_default", "role": "orchestrator", "provider": "openai", "model": "gpt-4.1-mini"},
        ],
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "behaviors": [
                {
                    "id": "merge_default",
                    "kind": "merge_and_conflict_resolution",
                    "agent": "orchestrator_default",
                    "enabled": True,
                }
            ],
            "branched": {"lanes": ["lane_a", "lane_b"]},
            "blue_red": {"loop_unit": "per_task"},
            "custom": {"cells": []},
        },
        "tasks": [
            {
                "id": "t1",
                "title": "acceptance task",
                "completed": False,
                "acceptance": {
                    "commands": list(acceptance_commands or []),
                    "required_artifacts": list(acceptance_artifacts or []),
                    "rubric": [],
                },
            }
        ],
        "outputs": {"required_artifacts": []},
    }
    return yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)


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


def test_acceptance_timeout_produces_typed_failure(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    plan = _single_task_plan(
        acceptance_commands=["python3 -c 'import time; time.sleep(2)'"],
        acceptance_timeout_seconds=1,
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    node = next(iter(run.nodes.values()))
    assert isinstance(node.result, dict)
    assert node.result.get("reason") == "acceptance_command_timeout"


def test_non_git_workspace_acceptance_uses_workspace_root(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    plan = _single_task_plan(acceptance_commands=["echo ok"])
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"


def test_acceptance_artifact_out_of_bounds_symlink_is_rejected(tmp_path: Path) -> None:
    leak_target = tmp_path.parent
    outside = leak_target / "outside_artifact.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        (tmp_path / "leak").symlink_to(leak_target)
    except OSError as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"symlink unavailable: {exc}")
    orch = LocalOrchestrator(tmp_path)
    plan = _single_task_plan(
        acceptance_artifacts=[{"id": "leak", "path_glob": "leak/outside_artifact.txt", "format": "file"}],
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    node = next(iter(run.nodes.values()))
    assert isinstance(node.result, dict)
    assert node.result.get("reason") == "acceptance_artifact_out_of_bounds"


def test_retry_policy_retries_transient_node_failures(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    original_run_node = orch._run_node
    failed_once = {"value": False}

    def flaky_run_node(self: LocalOrchestrator, handle, node, git_manager):  # type: ignore[no-untyped-def]
        if node.role == "worker" and not failed_once["value"]:
            failed_once["value"] = True
            return "failure", {"reason": "runtime_error", "error": "transient"}
        return original_run_node(handle, node, git_manager)

    orch._run_node = MethodType(flaky_run_node, orch)  # type: ignore[method-assign]
    plan = _single_task_plan(max_retries_per_node=1)
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.retry_count >= 1
    assert any(evt.get("event") == "NODE_RETRY_SCHEDULED" for evt in run.events)


def test_retry_policy_does_not_retry_deterministic_artifact_missing(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    plan = _single_task_plan(
        acceptance_artifacts=[{"id": "missing", "path_glob": "missing/*.txt", "format": "file"}],
        max_retries_per_node=3,
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    assert not any(evt.get("event") == "NODE_RETRY_SCHEDULED" for evt in run.events)
    node = next(iter(run.nodes.values()))
    assert node.attempt_count == 1
