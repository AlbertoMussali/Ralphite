from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

import pytest
from typer.testing import CliRunner

from ralphite.engine import LocalOrchestrator
from ralphite.cli.cli import (
    RECOVER_EXIT_INVALID_INPUT,
    RECOVER_EXIT_NO_RECOVERABLE,
    RECOVER_EXIT_PREFLIGHT_FAILED,
    RECOVER_EXIT_SUCCESS,
    app,
)


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
    payload = json.loads(result.stdout)
    assert payload["data"]["recovery_mode"] == "manual"
    assert payload["data"]["recovery_mode_label"] == "Manual"
    assert payload["data"]["recommended_recovery_mode"] == "manual"
    assert payload["data"]["recommended_recovery_mode_label"] == "Manual"
    assert (
        "Resolve merge markers manually"
        in payload["data"]["recommended_recovery_reason"]
    )
    assert payload["data"]["preflight"]["ok"] is True


def test_cli_recover_preflight_reports_blockers_and_mode(tmp_path: Path) -> None:
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
            "agent_best_effort",
            "--preflight-only",
            "--json",
        ],
    )
    assert result.exit_code == RECOVER_EXIT_PREFLIGHT_FAILED
    payload = json.loads(result.stdout)
    assert payload["data"]["recovery_mode"] == "agent_best_effort"
    assert payload["data"]["recovery_mode_label"] == "Best Effort Agent"
    assert payload["data"]["recommended_recovery_mode"] == "manual"
    assert payload["data"]["recommended_recovery_mode_label"] == "Manual"
    assert (
        "Resolve merge markers manually"
        in payload["data"]["recommended_recovery_reason"]
    )
    blockers = payload["data"]["preflight"]["blocking_reasons"]
    assert any("requires a non-empty prompt" in item for item in blockers)


def test_cli_recover_no_resume_table_shows_selected_mode(tmp_path: Path) -> None:
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
            "--no-resume",
            "--output",
            "table",
        ],
    )
    assert result.exit_code != RECOVER_EXIT_SUCCESS
    assert "Recovery mode:" in result.stdout
    assert "Recommended recovery mode:" in result.stdout
    assert "Manual" in result.stdout
    assert "Not Selected" not in result.stdout


def test_cli_recover_preflight_recommends_agent_best_effort_when_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = LocalOrchestrator(tmp_path)

    def fake_preflight(_run_id: str) -> dict[str, object]:
        return {
            "ok": False,
            "checks": [],
            "blocking_reasons": [],
            "conflict_files": [],
            "unresolved_conflict_files": [],
            "next_commands": [],
        }

    monkeypatch.setattr(orch, "recovery_preflight", fake_preflight)

    class _Run:
        plan_path = str(tmp_path / ".ralphite" / "plans" / "starter_bugfix.yaml")
        metadata = {
            "recovery": {
                "details": {"reason": "base_merge_conflict"},
                "prompt": "resolve conflicts",
            },
            "run_metrics": {"failure_reason_counts": {}},
        }

    monkeypatch.setattr(orch, "get_run", lambda _run_id: _Run())
    monkeypatch.setattr(orch, "recover_run", lambda _run_id: True)
    monkeypatch.setattr(
        orch, "set_recovery_mode", lambda _run_id, _mode, prompt=None: True
    )

    import ralphite.cli.commands.recover_cmd as recover_mod

    monkeypatch.setattr(recover_mod, "_orchestrator", lambda _workspace: orch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "recover",
            "--workspace",
            str(tmp_path),
            "--run-id",
            "run-123",
            "--mode",
            "manual",
            "--preflight-only",
            "--json",
        ],
    )
    assert result.exit_code == RECOVER_EXIT_PREFLIGHT_FAILED
    payload = json.loads(result.stdout)
    assert payload["data"]["recommended_recovery_mode"] == "agent_best_effort"
    assert payload["data"]["recommended_recovery_mode_label"] == "Best Effort Agent"


def test_cli_recover_preflight_recommends_abort_phase_for_phase_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = LocalOrchestrator(tmp_path)

    def fake_preflight(_run_id: str) -> dict[str, object]:
        return {
            "ok": False,
            "checks": [],
            "blocking_reasons": [],
            "conflict_files": [],
            "unresolved_conflict_files": [],
            "next_commands": [],
        }

    monkeypatch.setattr(orch, "recovery_preflight", fake_preflight)

    class _Run:
        plan_path = str(tmp_path / ".ralphite" / "plans" / "starter_bugfix.yaml")
        metadata = {
            "recovery": {
                "details": {"reason": "worktree_prepare_failed"},
            },
            "run_metrics": {"failure_reason_counts": {}},
        }

    monkeypatch.setattr(orch, "get_run", lambda _run_id: _Run())
    monkeypatch.setattr(orch, "recover_run", lambda _run_id: True)
    monkeypatch.setattr(
        orch, "set_recovery_mode", lambda _run_id, _mode, prompt=None: True
    )

    import ralphite.cli.commands.recover_cmd as recover_mod

    monkeypatch.setattr(recover_mod, "_orchestrator", lambda _workspace: orch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "recover",
            "--workspace",
            str(tmp_path),
            "--run-id",
            "run-123",
            "--mode",
            "manual",
            "--preflight-only",
            "--json",
        ],
    )
    assert result.exit_code == RECOVER_EXIT_PREFLIGHT_FAILED
    payload = json.loads(result.stdout)
    assert payload["data"]["recommended_recovery_mode"] == "abort_phase"
    assert payload["data"]["recommended_recovery_mode_label"] == "Abort Phase"


def test_cli_recover_requires_git_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()
    plain = Path(tempfile.mkdtemp())
    runner = CliRunner()
    result = runner.invoke(app, ["recover", "--workspace", str(plain), "--json"])
    assert result.exit_code == RECOVER_EXIT_INVALID_INPUT
    payload = json.loads(result.stdout)
    assert any(item.get("code") == "git.required" for item in payload.get("issues", []))
