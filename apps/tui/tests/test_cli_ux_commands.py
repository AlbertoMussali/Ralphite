from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

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
    provider: codex
    model: gpt-5.3-codex
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


def test_validate_non_v5_returns_version_invalid(tmp_path: Path) -> None:
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
    assert any(item.get("code") == "version.invalid" for item in payload.get("issues", []))


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
    seen: list[tuple[list[str], str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, cwd, check, capture_output, text):  # noqa: ANN001
        seen.append((list(command), str(cwd)))
        return _Result()

    monkeypatch.setattr(cli_mod.subprocess, "run", fake_run)
    ok, results = cli_mod._run_release_gate(
        repo_root=Path(__file__).resolve().parents[3],
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert all(isinstance(row.get("suite"), str) and row.get("suite") for row in results)
    commands = [" ".join(row) for row, _cwd in seen]
    assert any("packages/engine/tests/test_fixture_plan_matrix.py" in row for row in commands)
    assert any("packages/engine/tests/test_dispatched_plan_consistency.py" in row for row in commands)
    assert any("apps/tui/tests/test_bootstrap_e2e.py" in row for row in commands)
    assert any("apps/tui/tests/test_run_setup_resolved_preview_contract.py" in row for row in commands)
    assert all(cwd.endswith("Ralphite") for _row, cwd in seen)


def test_check_release_gate_ignores_doctor_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {"ok": False, "checks": [], "plan_failures": [], "stale_artifacts": {}, "fix_suggestions": []}

    def fake_release_gate(*, repo_root, quiet, machine_mode, verbose):  # noqa: ANN001
        return True, [{"suite": "fake", "command": "pytest -q", "cwd": str(repo_root), "exit_code": 0}]

    monkeypatch.setattr(cli_mod, "_doctor_snapshot", fake_snapshot)
    monkeypatch.setattr(cli_mod, "_run_release_gate", fake_release_gate)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--release-gate", "--output", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"


def test_check_beta_gate_fails_when_doctor_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {"ok": False, "checks": [], "plan_failures": [], "stale_artifacts": {}, "fix_suggestions": []}

    monkeypatch.setattr(cli_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--beta-gate", "--output", "json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(item.get("code") == "check.beta_gate_doctor_failed" for item in payload.get("issues", []))


def test_check_beta_gate_runs_backend_and_release_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {"ok": True, "checks": [], "plan_failures": [], "stale_artifacts": {}, "fix_suggestions": []}

    def fake_backend_smoke(*, orch, repo_root, quiet, machine_mode, verbose):  # noqa: ANN001
        return True, [{"suite": "backend-codex-smoke", "command": "codex exec", "cwd": str(repo_root), "exit_code": 0}]

    def fake_release_gate(*, repo_root, quiet, machine_mode, verbose):  # noqa: ANN001
        return True, [{"suite": "release", "command": "pytest -q", "cwd": str(repo_root), "exit_code": 0}]

    monkeypatch.setattr(cli_mod, "_doctor_snapshot", fake_snapshot)
    monkeypatch.setattr(cli_mod, "_run_backend_smoke", fake_backend_smoke)
    monkeypatch.setattr(cli_mod, "_run_release_gate", fake_release_gate)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--beta-gate", "--output", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    commands = payload.get("data", {}).get("commands", [])
    assert any(row.get("suite") == "backend-codex-smoke" for row in commands if isinstance(row, dict))
