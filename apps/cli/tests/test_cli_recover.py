from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ralphite_engine import LocalOrchestrator
from ralphite_cli.cli import (
    RECOVER_EXIT_INVALID_INPUT,
    RECOVER_EXIT_NO_RECOVERABLE,
    RECOVER_EXIT_SUCCESS,
    app,
)


def _plan_content() -> str:
    return """
version: 5
plan_id: cli_recovery
name: cli_recovery
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


def test_cli_recover_returns_no_recoverable_code(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["recover", "--workspace", str(tmp_path), "--json"])
    assert result.exit_code == RECOVER_EXIT_NO_RECOVERABLE


def test_cli_recover_returns_invalid_mode_code(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["recover", "--workspace", str(tmp_path), "--mode", "invalid_mode", "--json"],
    )
    assert result.exit_code == RECOVER_EXIT_INVALID_INPUT


def test_cli_recover_preflight_only_success(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    marker = tmp_path / ".ralphite" / "force_merge_conflict"
    marker.write_text("phase-1", encoding="utf-8")
    run_id = orch.start_run(plan_content=_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "recover",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--mode",
            "manual",
            "--preflight-only",
            "--json",
        ],
    )
    assert result.exit_code == RECOVER_EXIT_SUCCESS
