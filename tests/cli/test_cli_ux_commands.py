from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from ralphite.engine.headless_agent import (
    build_codex_exec_command,
    build_cursor_exec_command,
)
from typer.testing import CliRunner

import ralphite.cli.checks.suites as suite_mod
import ralphite.cli.commands.check_cmd as check_mod
import ralphite.cli.commands.quickstart_cmd as quickstart_mod
from ralphite.cli.cli import app
from ralphite.cli.core import _orchestrator


def _broken_v1_missing_worker(plan_id: str) -> str:
    return f"""
version: 1
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


def test_quickstart_json_output(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
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
    broken.write_text(_broken_v1_missing_worker("broken"), encoding="utf-8")

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
    assert any(
        item.get("code") == "fix.add_default_worker"
        for item in payload["data"]["fixes"]
    )


def test_validate_apply_safe_fixes_writes_revision(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    broken = plans / "broken2.yaml"
    broken.write_text(_broken_v1_missing_worker("broken2"), encoding="utf-8")

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


def test_validate_non_v1_returns_version_invalid(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    invalid_plan = plans / "invalid_v4.yaml"
    invalid_plan.write_text(
        """
version: 4
plan_id: invalid_plan
name: invalid_plan
tasks: []
""",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(invalid_plan),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert any(
        item.get("code") == "version.invalid" for item in payload.get("issues", [])
    )


def test_quickstart_table_output_shows_run_id_and_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "table",
        ],
    )
    assert result.exit_code == 0
    assert "Run ID:" in result.stdout
    assert "Artifacts" in result.stdout
    assert "Quickstart Preflight" in result.stdout
    assert "All tools declared by the selected plan." in result.stdout
    assert "All MCP servers declared by the selected plan." in result.stdout
    assert "Starting execution..." in result.stdout
    assert "['tool:*']" not in result.stdout


def test_run_table_output_shows_run_id_and_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["run", "--workspace", str(tmp_path), "--yes", "--output", "table"]
    )
    assert result.exit_code == 0
    assert "Run ID:" in result.stdout
    assert "Artifacts" in result.stdout
    assert "Run Preflight" in result.stdout
    assert "All tools declared by the selected plan." in result.stdout
    assert "All MCP servers declared by the selected plan." in result.stdout
    assert "['tool:*']" not in result.stdout


def test_quickstart_non_strict_allows_noncritical_doctor_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    monkeypatch.setattr(quickstart_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"


def test_quickstart_strict_doctor_blocks_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [
                {
                    "check": "stale-artifacts",
                    "status": "WARN",
                    "detail": "worktrees=1 branches=0",
                }
            ],
            "plan_failures": [],
            "stale_artifacts": {
                "stale_worktrees": [{"run_id": "x"}],
                "stale_branches": [],
            },
            "fix_suggestions": [],
        }

    monkeypatch.setattr(quickstart_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
            "--strict-doctor",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(
        item.get("code") == "doctor.failed" for item in payload.get("issues", [])
    )


def test_strict_checks_include_fixture_confidence_suites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], str]] = []
    monkeypatch.setenv("RALPHITE_SKIP_BACKEND_CMD_CHECKS", "1")

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, cwd, check, capture_output, text, env):  # noqa: ANN001
        assert "RALPHITE_SKIP_BACKEND_CMD_CHECKS" not in env
        seen.append((list(command), str(cwd)))
        return _Result()

    monkeypatch.setattr(suite_mod.subprocess, "run", fake_run)
    ok, results = suite_mod._run_strict_checks(
        repo_root=Path(__file__).resolve().parents[2],
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert all(
        isinstance(row.get("suite"), str) and row.get("suite") for row in results
    )
    commands = [" ".join(row) for row, _cwd in seen]
    assert any("tests/engine/test_fixture_plan_matrix.py" in row for row in commands)
    assert any(
        "tests/engine/test_dispatched_plan_consistency.py" in row for row in commands
    )
    assert any("tests/engine/test_examples_plans.py" in row for row in commands)
    assert any("tests/cli/test_bootstrap_e2e.py" in row for row in commands)
    assert all(cwd.endswith("Ralphite") for _row, cwd in seen)


def test_check_strict_fails_when_doctor_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": False,
            "checks": [],
            "plan_failures": [],
            "stale_artifacts": {},
            "fix_suggestions": [],
        }

    monkeypatch.setattr(check_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--strict", "--output", "json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(
        item.get("code") == "check.strict_doctor_failed"
        for item in payload.get("issues", [])
    )


def test_check_strict_runs_backend_and_strict_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [],
            "plan_failures": [],
            "stale_artifacts": {},
            "fix_suggestions": [],
        }

    def fake_backend_smoke(*, orch, repo_root, quiet, machine_mode, verbose):  # noqa: ANN001
        return True, [
            {
                "suite": "backend-codex-smoke",
                "command": "codex exec",
                "cwd": str(repo_root),
                "exit_code": 0,
            }
        ]

    def fake_strict_checks(*, repo_root, quiet, machine_mode, verbose):  # noqa: ANN001
        return True, [
            {
                "suite": "release",
                "command": "pytest -q",
                "cwd": str(repo_root),
                "exit_code": 0,
            }
        ]

    monkeypatch.setattr(check_mod, "_doctor_snapshot", fake_snapshot)
    monkeypatch.setattr(check_mod, "_run_backend_smoke", fake_backend_smoke)
    monkeypatch.setattr(check_mod, "_run_strict_checks", fake_strict_checks)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--strict", "--output", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    commands = payload.get("data", {}).get("commands", [])
    assert any(
        row.get("suite") == "backend-codex-smoke"
        for row in commands
        if isinstance(row, dict)
    )


def test_backend_smoke_codex_command_matches_runtime_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch.config.default_backend = "codex"
    orch.config.default_model = "gpt-5.3-codex"
    orch.config.default_reasoning_effort = "medium"
    repo_root = Path(__file__).resolve().parents[2]
    seen: dict[str, list[str]] = {}

    def fake_run(command, cwd, check, capture_output, text):  # noqa: ANN001
        seen["command"] = list(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}\n',
            stderr="",
        )

    monkeypatch.setattr(suite_mod.subprocess, "run", fake_run)
    ok, _results = suite_mod._run_backend_smoke(
        orch=orch,
        repo_root=repo_root,
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert seen["command"] == build_codex_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        reasoning_effort="medium",
        worktree=repo_root,
        sandbox="read-only",
    )


def test_backend_smoke_cursor_command_matches_runtime_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch.config.default_backend = "cursor"
    orch.config.default_model = "gpt-5.3-codex"
    orch.config.cursor_command = "agent"
    repo_root = Path(__file__).resolve().parents[2]
    seen: dict[str, list[str]] = {}

    def fake_run(command, cwd, check, capture_output, text):  # noqa: ANN001
        seen["command"] = list(command)
        return subprocess.CompletedProcess(
            command, 0, stdout='{"text":"OK"}\n', stderr=""
        )

    monkeypatch.setattr(suite_mod.subprocess, "run", fake_run)
    ok, _results = suite_mod._run_backend_smoke(
        orch=orch,
        repo_root=repo_root,
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert seen["command"] == build_cursor_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        cursor_command="agent",
        force=True,
    )
    assert "--force" in seen["command"]


def test_backend_smoke_is_skipped_when_env_requests_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    repo_root = Path(__file__).resolve().parents[2]

    def fail_run(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError(
            "subprocess.run should not be called when backend checks are skipped"
        )

    monkeypatch.setenv("RALPHITE_SKIP_BACKEND_CMD_CHECKS", "1")
    monkeypatch.setattr(suite_mod.subprocess, "run", fail_run)
    ok, results = suite_mod._run_backend_smoke(
        orch=orch,
        repo_root=repo_root,
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert results
    assert results[0]["suite"] == "backend-smoke-skipped"
    assert results[0]["exit_code"] == 0


def test_run_json_propagates_backend_overrides(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "--workspace",
            str(tmp_path),
            "--backend",
            "cursor",
            "--model",
            "gpt-5.3-codex",
            "--reasoning-effort",
            "high",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["data"]["backend"] == "cursor"
    assert payload["data"]["model"] == "gpt-5.3-codex"
    assert payload["data"]["reasoning_effort"] == "high"


def test_quickstart_json_propagates_backend_overrides(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--backend",
            "cursor",
            "--model",
            "gpt-5.3-codex",
            "--reasoning-effort",
            "high",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["data"]["backend"] == "cursor"
    assert payload["data"]["model"] == "gpt-5.3-codex"
    assert payload["data"]["reasoning_effort"] == "high"
