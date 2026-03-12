from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from ralphite.engine import LocalOrchestrator


def _init_repo(path: Path) -> None:
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


@pytest.fixture(autouse=True)
def _git_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)


def _plan_content() -> str:
    return """
version: 1
plan_id: e2e_recovery
name: e2e_recovery
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
