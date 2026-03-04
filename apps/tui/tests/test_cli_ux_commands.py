from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ralphite_tui.cli import app


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
    broken.write_text(
        """
version: 4
plan_id: broken
name: broken
run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default
constraints:
  max_parallel: 1
agents:
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
    title: invalid
    completed: false
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
            str(broken),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "validate"
    assert "fixes" in payload["data"]


def test_validate_apply_safe_fixes_writes_revision(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    broken = plans / "broken2.yaml"
    broken.write_text(
        """
version: 4
plan_id: broken2
name: broken2
run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default
constraints:
  max_parallel: 1
agents:
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
    title: invalid
    completed: false
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
