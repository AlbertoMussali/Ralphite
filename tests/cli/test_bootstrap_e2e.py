from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from typer.testing import CliRunner

import ralphite.cli.commands.quickstart_cmd as quickstart_mod
from ralphite.cli.cli import app


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


def test_quickstart_bootstrap_succeeds_and_initializes_workspace(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--bootstrap",
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
    data = payload.get("data", {})
    assert isinstance(data.get("bootstrap_paths"), list)
    assert isinstance(data.get("total_elapsed_seconds"), (int, float))


def test_quickstart_no_bootstrap_fails_with_doctor_guidance(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--no-bootstrap",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(
        item.get("code") == "doctor.failed" for item in payload.get("issues", [])
    )
    assert any("doctor" in action.lower() for action in payload.get("next_actions", []))


def test_quickstart_strict_doctor_blocks_warned_environment(
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
                "stale_worktrees": [{"run_id": "r1"}],
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
            "--bootstrap",
            "--strict-doctor",
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
    assert {
        "Doctor",
        "Bootstrap",
        "Plan Selection",
        "Capability Approval",
        "Run",
    }.issubset(set(steps))
    artifacts = data.get("artifacts", [])
    assert isinstance(artifacts, list)
    assert any(
        isinstance(item, dict) and item.get("id") == "machine_bundle"
        for item in artifacts
    )


@pytest.mark.parametrize(
    ("template", "expected_orchestration"),
    [
        ("starter_bugfix", "blue_red"),
        ("starter_refactor", "general_sps"),
        ("starter_docs_update", "general_sps"),
        ("starter_release_prep", "branched"),
    ],
)
def test_init_bootstrap_generates_v1_plan_for_template(
    tmp_path: Path, template: str, expected_orchestration: str
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--template",
            template,
            "--plan-id",
            f"plan_{template}",
            "--name",
            f"Plan {template}",
        ],
    )
    assert result.exit_code == 0
    plan_path = tmp_path / ".ralphite" / "plans" / f"plan_{template}.yaml"
    assert plan_path.exists()
    content = plan_path.read_text(encoding="utf-8")
    assert "version: 1" in content
    assert f"plan_id: plan_{template}" in content
    assert f"template: {expected_orchestration}" in content


@pytest.mark.parametrize("template", ["general_sps", "branched", "blue_red", "custom"])
def test_init_legacy_template_names_preserve_orchestration_shape(
    tmp_path: Path, template: str
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--template",
            template,
            "--plan-id",
            f"plan_{template}",
            "--name",
            f"Plan {template}",
        ],
    )
    assert result.exit_code == 0
    plan_path = tmp_path / ".ralphite" / "plans" / f"plan_{template}.yaml"
    assert plan_path.exists()
    content = plan_path.read_text(encoding="utf-8")
    assert f"plan_id: plan_{template}" in content
    assert f"template: {template}" in content


def test_validate_non_v1_returns_version_invalid(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    invalid = plans / "invalid.yaml"
    invalid.write_text("version: 4\nplan_id: x\nname: x\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(invalid),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert any(
        item.get("code") == "version.invalid" for item in payload.get("issues", [])
    )
