from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

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
