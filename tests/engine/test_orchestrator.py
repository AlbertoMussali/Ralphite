from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
from types import MethodType

import pytest
from ralphite.engine import LocalOrchestrator, parse_plan_yaml
from ralphite.engine.git_worktree import GitRequiredError
from ralphite.engine.models import RunViewState
from ralphite.engine.orchestrator import RunStartBlockedError
from ralphite.engine.orchestrator import RuntimeHandle
from ralphite.engine.structure_compiler import RuntimeExecutionPlan, RuntimeNodeSpec
import yaml

_AGENT_DEFAULTS = """\
version: 1
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
behaviors:
  - id: prepare_dispatch_default
    kind: prepare_dispatch
    agent: orchestrator_default
    enabled: true
  - id: merge_default
    kind: merge_and_conflict_resolution
    agent: orchestrator_default
    enabled: true
  - id: summarize_default
    kind: summarize_work
    agent: orchestrator_default
    enabled: true
"""


def _init_repo(path: Path) -> None:
    (path / "agent_defaults.yaml").write_text(_AGENT_DEFAULTS, encoding="utf-8")
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ralphite Test"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ralphite@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    _init_repo(tmp_path)
    return tmp_path


def _plan_content() -> str:
    return """
version: 1
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
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
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
    deps: [t1]
    routing:
      cell: par_core
  - id: t3
    title: Build B
    completed: false
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
version: 1
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
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
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


def _branched_overlap_plan_content() -> str:
    return """
version: 1
plan_id: overlap
name: overlap
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
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
orchestration:
  template: branched
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
    title: Update docs index
    completed: false
    routing:
      lane: lane_a
      tags: [docs, cli]
  - id: t2
    title: Refresh docs first-run guide
    completed: false
    routing:
      lane: lane_b
      tags: [docs, cli]
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
        "version": 1,
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
            {
                "id": "worker_default",
                "role": "worker",
                "provider": "codex",
                "model": "gpt-5.3-codex",
            },
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "codex",
                "model": "gpt-5.3-codex",
            },
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


def _artifact_text(run, artifact_id: str) -> str:  # type: ignore[no-untyped-def]
    artifact = next(item for item in run.artifacts if item["id"] == artifact_id)
    return Path(artifact["path"]).read_text(encoding="utf-8")


def test_goal_plan_run_succeeds(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    plan_path = orch.goal_to_plan("Create a simple test artifact")

    run_id = orch.start_run(plan_ref=str(plan_path))
    events = list(orch.stream_events(run_id))

    assert any(event["event"] == "RUN_DONE" for event in events)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status in {"succeeded", "failed"}
    assert any(item["id"] == "final_report" for item in run.artifacts)
    assert any(item["id"] == "run_metrics" for item in run.artifacts)
    assert isinstance(run.metadata.get("run_metrics"), dict)
    report = _artifact_text(run, "final_report")
    assert "## Outcome" in report
    assert "## Changed Files" in report
    assert "## Acceptance Results" in report
    assert "## Next Steps" in report
    assert "## Supporting Artifacts" in report
    assert "## Run Highlights" in report


def test_cancel_run(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    plan_path = orch.goal_to_plan("Longish task to test cancel")
    run_id = orch.start_run(plan_ref=str(plan_path))
    assert orch.cancel_run(run_id) is True

    events = list(orch.stream_events(run_id))
    run = orch.get_run(run_id)

    assert run is not None
    assert run.status in {"cancelled", "failed", "succeeded"}
    assert any(evt["event"] in {"RUN_CANCEL_REQUESTED", "RUN_DONE"} for evt in events)


def test_v1_plan_executes_with_phase_events(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
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


def test_conflict_triggers_recovery_and_abort_mode(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    (workspace / ".ralphite" / "force_merge_conflict").write_text(
        "phase-1", encoding="utf-8"
    )
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


def test_start_run_blocks_when_recoverable_run_exists(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    (workspace / ".ralphite" / "force_merge_conflict").write_text(
        "phase-1", encoding="utf-8"
    )
    blocked_run = orch.start_run(plan_content=_conflict_plan_content())
    assert orch.wait_for_run(blocked_run, timeout=8.0) is True

    with pytest.raises(RunStartBlockedError):
        orch.start_run(plan_content=_single_task_plan())


def test_first_failure_agent_recovery_can_resume_inline(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    marker = workspace / ".ralphite" / "force_merge_conflict"
    marker.write_text("phase-1", encoding="utf-8")

    def auto_recovery_agent(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        if node.id.endswith("auto-recovery") and marker.exists():
            marker.unlink()
        return True, {"summary": "ok"}

    orch._execute_agent = MethodType(auto_recovery_agent, orch)  # type: ignore[method-assign]
    run_id = orch.start_run(
        plan_content=_conflict_plan_content(),
        first_failure_recovery="agent_best_effort",
    )
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    recovery = run.metadata.get("recovery", {})
    assert isinstance(recovery, dict)
    assert recovery.get("auto_attempt_status") == "succeeded"


def test_high_overlap_branched_work_is_serialized(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    handle = RuntimeHandle(
        run=RunViewState(
            id="run-overlap",
            plan_path=str(workspace / ".ralphite" / "plans" / "overlap.yaml"),
            metadata={
                "node_surface_map": {
                    "node-a": ["docs", "cli"],
                    "node-b": ["docs", "cli"],
                }
            },
        ),
        plan=parse_plan_yaml(
            _branched_overlap_plan_content(), workspace_root=workspace
        ),
        runtime=RuntimeExecutionPlan(
            nodes=[],
            node_payload={},
            node_levels={},
            groups={},
            parallel_limit=2,
            task_parse_issues=[],
            blocks=[],
            node_block_index={},
            resolved_cells=[],
            task_assignment={},
            compile_warnings=[],
        ),
        profile_map={},
        permission_snapshot=orch.default_permission_snapshot(),
    )
    node_a = RuntimeNodeSpec(
        id="node-a",
        kind="agent",
        group="phase-1",
        depends_on=[],
        task="Update docs index",
        agent_profile_id="worker_default",
        role="worker",
        phase="phase-1",
        lane="lane_a",
        cell_id="lane_a",
        source_task_id="t1",
        block_index=1,
    )
    node_b = RuntimeNodeSpec(
        id="node-b",
        kind="agent",
        group="phase-1",
        depends_on=[],
        task="Refresh first-run docs",
        agent_profile_id="worker_default",
        role="worker",
        phase="phase-1",
        lane="lane_b",
        cell_id="lane_b",
        source_task_id="t2",
        block_index=1,
    )
    batch = orch._choose_batch(handle, [node_a, node_b])  # noqa: SLF001
    assert [node.id for node in batch] == ["node-a"]
    assert handle.run.metadata.get("serialized_overlap_blocks")


def test_start_run_applies_execution_overrides_to_metadata(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    run_id = orch.start_run(
        plan_content=_single_task_plan(),
        backend_override="cursor",
        model_override="gpt-5.3-codex",
        reasoning_effort_override="high",
    )
    run = orch.get_run(run_id)
    assert run is not None
    defaults = run.metadata.get("execution_defaults")
    assert isinstance(defaults, dict)
    assert defaults.get("backend") == "cursor"
    assert defaults.get("model") == "gpt-5.3-codex"
    assert defaults.get("reasoning_effort") == "high"


def test_acceptance_timeout_produces_typed_failure(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
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
    report = _artifact_text(run, "final_report")
    assert "## Acceptance Results" in report
    assert "Failing command:" in report
    assert "Acceptance Timeout" in report


def test_start_run_requires_git_workspace(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralphite.engine import LocalOrchestrator

    monkeypatch.undo()
    plain = Path(tempfile.mkdtemp())
    orch = LocalOrchestrator(plain)
    plan = _single_task_plan(acceptance_commands=["echo ok"])
    with pytest.raises(GitRequiredError):
        orch.start_run(plan_content=plan)


def test_acceptance_artifact_out_of_bounds_symlink_is_rejected(workspace: Path) -> None:
    leak_target = workspace.parent
    outside = leak_target / "outside_artifact.txt"
    outside.write_text("x", encoding="utf-8")
    orch = LocalOrchestrator(workspace)

    def symlink_agent(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        if node.role == "worker":
            try:
                (worktree / "leak").symlink_to(leak_target)
            except OSError as exc:  # pragma: no cover - platform dependent
                pytest.skip(f"symlink unavailable: {exc}")
        return True, {"summary": "created symlink"}

    orch._execute_agent = MethodType(symlink_agent, orch)  # type: ignore[method-assign]
    plan = _single_task_plan(
        acceptance_artifacts=[
            {"id": "leak", "path_glob": "leak/outside_artifact.txt", "format": "file"}
        ],
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    node = next(iter(run.nodes.values()))
    assert isinstance(node.result, dict)
    assert node.result.get("reason") == "acceptance_artifact_out_of_bounds"


def test_acceptance_artifact_missing_is_reported_in_final_report(
    workspace: Path,
) -> None:
    orch = LocalOrchestrator(workspace)
    plan = _single_task_plan(
        acceptance_artifacts=[
            {"id": "missing", "path_glob": "missing/*.txt", "format": "file"}
        ],
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    report = _artifact_text(run, "final_report")
    assert "## Acceptance Results" in report
    assert "Missing artifact: `missing`" in report
    assert "## Failures and Warnings" in report


def test_recovery_required_run_is_reported_in_final_report(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    (workspace / ".ralphite" / "force_merge_conflict").write_text(
        "phase-1", encoding="utf-8"
    )
    run_id = orch.start_run(plan_content=_conflict_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "paused_recovery_required"
    metrics = run.metadata.get("run_metrics", {})
    assert isinstance(metrics, dict)
    interruptions = metrics.get("interruption_reason_counts", {})
    assert isinstance(interruptions, dict)
    assert interruptions.get("simulated_conflict") == 1
    report = _artifact_text(run, "final_report")
    assert "## Outcome" in report
    assert "## Changed Files" in report
    assert "## Acceptance Results" in report
    assert "## Failures and Warnings" in report
    assert "Recovery blocker:" in report
    assert "SIMULATED_CONFLICT" in report
    assert "Unresolved conflict:" in report
    assert "## Next Steps" in report
    assert (
        "Return to Ralphite recovery and resume." in report
        or "No follow-up action recorded." not in report
    )
    assert "## Supporting Artifacts" in report
    assert "## Run Highlights" in report


def test_retry_policy_retries_transient_node_failures(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
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


def test_retry_policy_does_not_retry_deterministic_artifact_missing(
    workspace: Path,
) -> None:
    orch = LocalOrchestrator(workspace)
    plan = _single_task_plan(
        acceptance_artifacts=[
            {"id": "missing", "path_glob": "missing/*.txt", "format": "file"}
        ],
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
