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
    write_policy: dict[str, object] | None = None,
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
                "write_policy": dict(write_policy or {}),
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


def test_merge_orchestrator_edits_phase_integration_worktree_not_base(
    workspace: Path,
) -> None:
    orch = LocalOrchestrator(workspace)
    merge_worktrees: list[str] = []

    def merge_safe_agent(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        target = worktree / "merge-note.md"
        if node.role == "worker":
            target.write_text("worker\n", encoding="utf-8")
        elif node.behavior_kind == "merge_and_conflict_resolution":
            merge_worktrees.append(str(worktree))
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(existing + "orchestrator\n", encoding="utf-8")
        return True, {"summary": "ok"}

    orch._execute_agent = MethodType(merge_safe_agent, orch)  # type: ignore[method-assign]
    run_id = orch.start_run(plan_content=_conflict_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert merge_worktrees
    assert all(str(path) != str(workspace) for path in merge_worktrees)
    assert all(path.endswith("integration") for path in merge_worktrees)
    merged_note = (workspace / "merge-note.md").read_text(encoding="utf-8")
    assert merged_note.startswith("worker\n")
    assert "orchestrator\n" in merged_note
    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=no"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""


def test_merge_orchestrator_backend_payload_failure_with_real_output_is_salvaged(
    workspace: Path,
) -> None:
    orch = LocalOrchestrator(workspace)

    def merge_then_fail(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        target = worktree / "merge-note.md"
        if node.role == "worker":
            target.write_text("worker\n", encoding="utf-8")
            return True, {"summary": "ok"}
        if node.behavior_kind == "merge_and_conflict_resolution":
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(existing + "orchestrator\n", encoding="utf-8")
            return False, {
                "reason": "backend_payload_missing",
                "error": "no final agent message",
            }
        return True, {"summary": "ok"}

    orch._execute_agent = MethodType(merge_then_fail, orch)  # type: ignore[method-assign]
    run_id = orch.start_run(plan_content=_conflict_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    merge_node = next(
        node
        for node in run.nodes.values()
        if isinstance(node.result, dict)
        and node.result.get("backend_failure_reason") == "backend_payload_missing"
    )
    assert merge_node.result.get("mode") == "backend_failure_salvaged"
    merged_note = (workspace / "merge-note.md").read_text(encoding="utf-8")
    assert merged_note.startswith("worker\n")
    assert "orchestrator\n" in merged_note


def test_merge_orchestrator_backend_nonzero_after_commit_is_salvaged(
    workspace: Path,
) -> None:
    orch = LocalOrchestrator(workspace)

    def commit_then_fail(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        target = worktree / "merge-note.md"
        if node.role == "worker":
            target.write_text("worker\n", encoding="utf-8")
            return True, {"summary": "ok"}
        if node.behavior_kind == "merge_and_conflict_resolution":
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(existing + "orchestrator\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-A"],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "manual orchestrator commit"],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            )
            return False, {
                "reason": "backend_nonzero",
                "error": "cursor exited after committing",
                "exit_code": 1,
            }
        return True, {"summary": "ok"}

    orch._execute_agent = MethodType(commit_then_fail, orch)  # type: ignore[method-assign]
    run_id = orch.start_run(plan_content=_conflict_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0) is True
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    merge_node = next(
        node
        for node in run.nodes.values()
        if isinstance(node.result, dict)
        and node.result.get("backend_failure_reason") == "backend_nonzero"
    )
    assert merge_node.result.get("mode") == "backend_failure_salvaged"
    merged_note = (workspace / "merge-note.md").read_text(encoding="utf-8")
    assert "orchestrator\n" in merged_note


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
        acceptance_commands=['uv run python -c "import time; time.sleep(2)"'],
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


def test_acceptance_failure_preserves_committed_worker_artifacts(
    workspace: Path,
) -> None:
    orch = LocalOrchestrator(workspace)
    plan = _single_task_plan(
        acceptance_commands=['uv run python -c "import sys; sys.exit(1)"']
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    retained = (
        run.metadata.get("retained_work", [])
        if isinstance(run.metadata.get("retained_work"), list)
        else []
    )
    assert retained
    first = retained[0]
    assert first.get("branch")
    assert first.get("commit")
    assert first.get("worktree_exists") is True
    assert any(evt.get("event") == "CLEANUP_SKIPPED" for evt in run.events)
    assert any(item["id"] == "salvage_bundle" for item in run.artifacts)
    report = _artifact_text(run, "final_report")
    assert "## Retained Work" in report
    assert "## Next Steps" in report
    assert "ralphite salvage" in report


def test_promote_salvage_promotes_retained_committed_worker(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = LocalOrchestrator(workspace)
    plan = _single_task_plan(
        acceptance_commands=['uv run python -c "import sys; sys.exit(1)"']
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    failed = orch.get_run(run_id)
    assert failed is not None
    retained = (
        failed.metadata.get("retained_work", [])
        if isinstance(failed.metadata.get("retained_work"), list)
        else []
    )
    assert retained
    node_id = str(retained[0]["node_id"])

    def passing_acceptance(self, node, commit_meta, *, timeout_seconds):  # type: ignore[no-untyped-def]
        return True, {
            "commands": [{"command": "promoted acceptance", "exit_code": 0}],
            "required_artifacts": [],
            "rubric": [],
        }

    monkeypatch.setattr(
        LocalOrchestrator,
        "_evaluate_acceptance",
        passing_acceptance,
    )
    ok, result = orch.promote_salvage(run_id, node_id)
    assert ok is True
    assert result["run_status"] == "succeeded"
    promoted = orch.get_run(run_id)
    assert promoted is not None
    assert promoted.status == "succeeded"
    assert promoted.nodes[node_id].status == "succeeded"
    assert promoted.nodes[node_id].result.get("mode") == "salvage_promoted"


def test_backend_failure_preserves_uncommitted_worker_changes(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)

    def failing_agent(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        if node.role == "worker":
            (worktree / "partial.txt").write_text("partial work\n", encoding="utf-8")
            return False, {
                "reason": "backend_nonzero",
                "error": "simulated backend failure",
                "worktree": str(worktree),
            }
        return True, {"summary": "ok"}

    orch._execute_agent = MethodType(failing_agent, orch)  # type: ignore[method-assign]
    run_id = orch.start_run(plan_content=_single_task_plan())
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    node = next(iter(run.nodes.values()))
    assert isinstance(node.result, dict)
    assert node.result.get("mode") == "backend_failure_salvaged"
    worktree_meta = node.result.get("worktree", {})
    assert isinstance(worktree_meta, dict)
    assert "partial.txt" in {
        str(item.get("path") or "")
        for item in worktree_meta.get("changed_files", [])
        if isinstance(item, dict)
    }


def test_backend_payload_malformed_with_valid_worker_output_is_salvaged(
    workspace: Path,
) -> None:
    orch = LocalOrchestrator(workspace)

    def malformed_agent(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        if node.role == "worker":
            (worktree / "docs").mkdir(exist_ok=True)
            (worktree / "docs" / "artifact.md").write_text(
                "artifact\n", encoding="utf-8"
            )
            return False, {
                "reason": "backend_payload_malformed",
                "error": "cursor returned malformed payload",
                "stdout_excerpt": "{bad json",
            }
        return True, {"summary": "ok"}

    orch._execute_agent = MethodType(malformed_agent, orch)  # type: ignore[method-assign]
    plan = _single_task_plan(
        acceptance_artifacts=[
            {"id": "artifact", "path_glob": "docs/*.md", "format": "file"}
        ]
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    node = next(iter(run.nodes.values()))
    assert node.status == "succeeded"
    assert isinstance(node.result, dict)
    assert node.result.get("mode") == "backend_failure_salvaged"
    assert node.result.get("backend_failure_reason") == "backend_payload_malformed"
    retained = (
        run.metadata.get("retained_work", [])
        if isinstance(run.metadata.get("retained_work"), list)
        else []
    )
    assert retained == []


def test_backend_nonzero_with_valid_worker_output_is_salvaged(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)

    def failing_agent(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        if node.role == "worker":
            (worktree / "docs").mkdir(exist_ok=True)
            (worktree / "docs" / "artifact.md").write_text(
                "artifact\n", encoding="utf-8"
            )
            return False, {
                "reason": "backend_nonzero",
                "error": "cursor exited 1 after writing files",
                "exit_code": 1,
                "stderr_excerpt": "cursor failed",
            }
        return True, {"summary": "ok"}

    orch._execute_agent = MethodType(failing_agent, orch)  # type: ignore[method-assign]
    plan = _single_task_plan(
        acceptance_artifacts=[
            {"id": "artifact", "path_glob": "docs/*.md", "format": "file"}
        ]
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"
    node = next(iter(run.nodes.values()))
    assert node.status == "succeeded"
    assert isinstance(node.result, dict)
    assert node.result.get("mode") == "backend_failure_salvaged"
    assert node.result.get("backend_failure_reason") == "backend_nonzero"


def test_worker_write_scope_is_enforced_from_git_evidence(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)

    def scoped_agent(self, handle, node, profile, snapshot, *, worktree):  # type: ignore[no-untyped-def]
        if node.role == "worker":
            (worktree / "docs").mkdir(exist_ok=True)
            (worktree / "docs" / "allowed.md").write_text("ok\n", encoding="utf-8")
            (worktree / "README.md").write_text("not allowed\n", encoding="utf-8")
        return True, {"summary": "wrote files"}

    orch._execute_agent = MethodType(scoped_agent, orch)  # type: ignore[method-assign]
    plan = _single_task_plan(
        write_policy={
            "allowed_write_roots": ["docs"],
            "allow_root_writes": False,
        }
    )
    run_id = orch.start_run(plan_content=plan)
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    node = next(iter(run.nodes.values()))
    assert isinstance(node.result, dict)
    assert node.result.get("reason") == "backend_out_of_worktree_mutation"
    diagnostics = node.result.get("diagnostics", {})
    assert isinstance(diagnostics, dict)
    assert "README.md" in diagnostics.get("out_of_scope_files", [])


def test_acceptance_commands_use_worker_subprocess_env(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = LocalOrchestrator(workspace)
    seen: dict[str, object] = {}

    def fake_env(*, worktree):  # noqa: ANN001
        seen["worktree"] = str(worktree)
        return {
            "UV_CACHE_DIR": r"C:\ralphite-cache\abc123",
            "TMP": r"C:\ralphite-cache\abc123\tmp",
            "TEMP": r"C:\ralphite-cache\abc123\tmp",
        }

    def fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        seen["env"] = dict(env)
        seen["cwd"] = str(cwd)
        seen["command"] = list(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(
        "ralphite.engine.orchestrator.build_worker_subprocess_env", fake_env
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    node = RuntimeNodeSpec(
        id="phase-1::task::acceptance",
        kind="agent",
        group="phase-1",
        depends_on=[],
        task="acceptance task",
        agent_profile_id="worker_default",
        role="worker",
        phase="phase-1",
        lane="sequential",
        cell_id="seq",
        source_task_id="t1",
        acceptance={"commands": ["echo ok"], "required_artifacts": [], "rubric": []},
    )

    ok, result = orch._evaluate_acceptance(
        node,
        {"worktree": str(workspace)},
        timeout_seconds=5,
    )

    assert ok is True
    assert result["commands"][0]["exit_code"] == 0
    assert seen["cwd"] == str(workspace)
    assert seen["worktree"] == str(workspace)
    assert seen["env"]["UV_CACHE_DIR"] == r"C:\ralphite-cache\abc123"
    assert seen["command"] == ["echo", "ok"]


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
