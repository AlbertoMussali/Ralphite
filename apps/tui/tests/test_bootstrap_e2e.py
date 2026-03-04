from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
from typer.testing import CliRunner

import ralphite_tui.cli as cli_mod
from ralphite_tui.cli import app


def _legacy_fixture_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / "packages" / "engine" / "tests" / "fixtures" / "plans" / "invalid_v4_legacy.yaml"


def test_quickstart_bootstrap_succeeds_and_initializes_workspace(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--bootstrap",
            "--no-tui",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "quickstart"
    assert payload["status"] == "succeeded"
    assert (tmp_path / ".ralphite" / "config.toml").exists()
    plans = list((tmp_path / ".ralphite" / "plans").glob("*.yaml"))
    assert plans


def test_quickstart_no_bootstrap_fails_with_doctor_guidance(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--no-bootstrap",
            "--no-tui",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(item.get("code") == "doctor.failed" for item in payload.get("issues", []))
    assert any("doctor" in action.lower() for action in payload.get("next_actions", []))


def test_quickstart_strict_doctor_blocks_warned_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [{"check": "stale-artifacts", "status": "WARN", "detail": "worktrees=1 branches=0"}],
            "plan_failures": [],
            "stale_artifacts": {"stale_worktrees": [{"run_id": "r1"}], "stale_branches": []},
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
            "--bootstrap",
            "--strict-doctor",
            "--no-tui",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"


def test_quickstart_surfaces_step_timing_and_artifacts(tmp_path: Path) -> None:
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
    data = payload.get("data", {})
    step_timing = data.get("step_timing", [])
    assert isinstance(step_timing, list) and step_timing
    steps = [str(item.get("step")) for item in step_timing if isinstance(item, dict)]
    assert {"Doctor", "Plan Selection", "Capability Approval", "Run"}.issubset(set(steps))
    artifacts = data.get("artifacts", [])
    assert isinstance(artifacts, list)
    assert any(isinstance(item, dict) and item.get("id") == "machine_bundle" for item in artifacts)


def test_validate_v4_fixture_returns_explicit_migrate_command(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    legacy = plans / "legacy_v4.yaml"
    shutil.copy2(_legacy_fixture_path(), legacy)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(legacy),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    commands = payload.get("data", {}).get("recommended_commands", [])
    actions = payload.get("next_actions", [])
    assert any("ralphite migrate" in item for item in commands)
    assert any("ralphite migrate" in item for item in actions)
