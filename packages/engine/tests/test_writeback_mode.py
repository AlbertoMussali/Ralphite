from __future__ import annotations

from pathlib import Path
import subprocess

from ralphite_engine import LocalOrchestrator


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)


def _plan_content() -> str:
    return """
version: 5
plan_id: writeback_mode
name: writeback_mode
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


def _prepare_repo(path: Path) -> None:
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Ralphite Test")
    _git(path, "config", "user.email", "ralphite@example.com")
    (path / ".gitignore").write_text(".ralphite/\n", encoding="utf-8")
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def test_revision_only_writeback_succeeds_with_ignored_ralphite(tmp_path: Path) -> None:
    _prepare_repo(tmp_path)
    orch = LocalOrchestrator(tmp_path)
    orch.config.task_writeback_mode = "revision_only"

    run_id = orch.start_run(plan_content=_plan_content())
    assert orch.wait_for_run(run_id, timeout=12.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"

    revisions = sorted((tmp_path / ".ralphite" / "plans").glob("completed.*.yaml"))
    assert revisions


def test_in_place_writeback_fails_when_plan_path_is_ignored(tmp_path: Path) -> None:
    _prepare_repo(tmp_path)
    orch = LocalOrchestrator(tmp_path)
    orch.config.task_writeback_mode = "in_place"

    plan_path = orch.paths["plans"] / "ignored_plan.yaml"
    plan_path.write_text(_plan_content(), encoding="utf-8")
    run_id = orch.start_run(plan_ref=str(plan_path))
    assert orch.wait_for_run(run_id, timeout=12.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"

    writeback_event = next((event for event in run.events if event.get("event") == "TASK_WRITEBACK_FAILED"), None)
    assert writeback_event is not None
    assert isinstance(writeback_event.get("meta"), dict)
    assert str(writeback_event["meta"].get("reason", "")) == "git_add_failed"


def test_disabled_writeback_skips_task_updates(tmp_path: Path) -> None:
    _prepare_repo(tmp_path)
    orch = LocalOrchestrator(tmp_path)
    orch.config.task_writeback_mode = "disabled"

    run_id = orch.start_run(plan_content=_plan_content())
    assert orch.wait_for_run(run_id, timeout=12.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"

    revisions = sorted((tmp_path / ".ralphite" / "plans").glob("completed.*.yaml"))
    assert revisions == []
