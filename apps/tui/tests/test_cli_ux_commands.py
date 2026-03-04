from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ralphite_engine import LocalOrchestrator
import ralphite_tui.cli as cli_mod
from ralphite_tui.cli import app


def _broken_v5_missing_worker(plan_id: str) -> str:
    return f"""
version: 5
plan_id: {plan_id}
name: {plan_id}
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
constraints:
  max_parallel: 1
agents:
  - id: orchestrator_default
    role: orchestrator
    provider: openai
    model: gpt-4.1-mini
tasks:
  - id: t1
    title: invalid
    completed: false
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
outputs:
  required_artifacts: []
"""


def test_quickstart_json_no_tui(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--no-tui",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "quickstart"
    assert payload["status"] == "succeeded"


def test_validate_command_returns_fixes_for_invalid_plan(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    broken = plans / "broken.yaml"
    broken.write_text(_broken_v5_missing_worker("broken"), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(broken),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "validate"
    assert "fixes" in payload["data"]
    assert any(item.get("code") == "fix.add_default_worker" for item in payload["data"]["fixes"])


def test_validate_apply_safe_fixes_writes_revision(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    broken = plans / "broken2.yaml"
    broken.write_text(_broken_v5_missing_worker("broken2"), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(broken),
            "--apply-safe-fixes",
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    fixed = payload["data"].get("fixed_revision")
    assert isinstance(fixed, str)
    assert Path(fixed).exists()


def test_migrate_command_converts_v4_to_v5(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    legacy = plans / "legacy.yaml"
    legacy.write_text(
        """
version: 4
plan_id: legacy
name: legacy
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
    title: legacy task
    completed: false
outputs:
  required_artifacts: []
""",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "migrate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(legacy),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "migrate"
    target = payload["data"].get("target_plan")
    assert isinstance(target, str)
    assert Path(target).exists()

    validate_result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            target,
            "--json",
        ],
    )
    assert validate_result.exit_code == 0


def test_validate_json_includes_resolved_execution(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    summary = payload["data"]["summary"]
    resolved = summary.get("resolved_execution", {})
    assert isinstance(resolved, dict)
    assert "template" in resolved
    assert isinstance(resolved.get("resolved_cells"), list)
    assert isinstance(resolved.get("resolved_nodes"), list)


def test_validate_v4_recommends_migrate_command(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    legacy = plans / "legacy_v4.yaml"
    legacy.write_text(
        """
version: 4
plan_id: legacy
name: legacy
tasks: []
""",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["validate", "--workspace", str(tmp_path), "--plan", str(legacy), "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    commands = payload["data"].get("recommended_commands", [])
    assert any("ralphite migrate" in item for item in commands)
    assert any("ralphite migrate" in item for item in payload.get("next_actions", []))


def test_quickstart_table_output_shows_run_id_and_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["quickstart", "--workspace", str(tmp_path), "--no-tui", "--yes", "--output", "table"],
    )
    assert result.exit_code == 0
    assert "Run ID:" in result.stdout
    assert "Artifacts:" in result.stdout


def test_run_table_output_shows_run_id_and_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["run", "--workspace", str(tmp_path), "--no-tui", "--yes", "--output", "table"],
    )
    assert result.exit_code == 0
    assert "Run ID:" in result.stdout
    assert "Artifacts:" in result.stdout


def test_quickstart_non_strict_allows_noncritical_doctor_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": False,
            "checks": [
                {"check": "recovery-readiness", "status": "FAIL", "detail": "degraded"},
                {"check": "plans", "status": "OK", "detail": "1"},
            ],
            "plan_failures": [],
            "stale_artifacts": {"stale_worktrees": [], "stale_branches": []},
            "fix_suggestions": [],
        }

    monkeypatch.setattr(cli_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["quickstart", "--workspace", str(tmp_path), "--no-tui", "--yes", "--output", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"


def test_quickstart_strict_doctor_blocks_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [{"check": "stale-artifacts", "status": "WARN", "detail": "worktrees=1 branches=0"}],
            "plan_failures": [],
            "stale_artifacts": {"stale_worktrees": [{"run_id": "x"}], "stale_branches": []},
            "fix_suggestions": [],
        }

    monkeypatch.setattr(cli_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--no-tui",
            "--yes",
            "--output",
            "json",
            "--strict-doctor",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(item.get("code") == "doctor.failed" for item in payload.get("issues", []))


def test_release_gate_includes_fixture_confidence_suites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orch = LocalOrchestrator(tmp_path)
    seen: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, cwd, check, capture_output, text):  # noqa: ANN001
        seen.append(list(command))
        return _Result()

    monkeypatch.setattr(cli_mod.subprocess, "run", fake_run)
    ok, _results = cli_mod._run_release_gate(orch, quiet=True, machine_mode=True, verbose=False)
    assert ok is True
    commands = [" ".join(row) for row in seen]
    assert any("packages/engine/tests/test_fixture_plan_matrix.py" in row for row in commands)
    assert any("packages/engine/tests/test_dispatched_plan_consistency.py" in row for row in commands)
    assert any("apps/tui/tests/test_bootstrap_e2e.py" in row for row in commands)
    assert any("apps/tui/tests/test_run_setup_resolved_preview_contract.py" in row for row in commands)
