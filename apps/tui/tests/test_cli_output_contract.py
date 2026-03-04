from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ralphite_engine import LocalOrchestrator
from ralphite_tui.cli import app


def _plan_content() -> str:
    return """
version: 4
plan_id: contract
name: contract
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
    tools_allow: [tool:*]
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
    title: Build
    completed: false
"""


def test_check_json_envelope_contains_schema_version(tmp_path: Path) -> None:
    LocalOrchestrator(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", "--workspace", str(tmp_path), "--output", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "check"
    assert isinstance(payload["data"].get("doctor"), dict)


def test_replay_json_envelope_contains_schema_version(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    run_id = orch.start_run(plan_content=_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["replay", run_id, "--workspace", str(tmp_path), "--no-tui", "--output", "json"],
    )
    assert result.exit_code in {0, 1}
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "replay"
    assert payload["run_id"]


def test_tui_json_requires_dry_run(tmp_path: Path) -> None:
    LocalOrchestrator(tmp_path)
    runner = CliRunner()
    bad = runner.invoke(app, ["tui", "--workspace", str(tmp_path), "--output", "json"])
    assert bad.exit_code != 0
    bad_payload = json.loads(bad.stdout)
    assert bad_payload["schema_version"] == "cli-output.v1"
    assert bad_payload["command"] == "tui"
    assert bad_payload["ok"] is False

    ok = runner.invoke(app, ["tui", "--workspace", str(tmp_path), "--output", "json", "--dry-run"])
    assert ok.exit_code == 0
    ok_payload = json.loads(ok.stdout)
    assert ok_payload["schema_version"] == "cli-output.v1"
    assert ok_payload["command"] == "tui"
    assert ok_payload["ok"] is True
