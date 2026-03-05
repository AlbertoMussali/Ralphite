from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from ralphite_engine import LocalOrchestrator
from ralphite_tui.cli import app


def _plan_content() -> str:
    return """
version: 5
plan_id: contract
name: contract
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


def _schema() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    schema_path = root / "packages" / "schemas" / "json" / "cli-output-v1.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _matches_json_type(value: object, schema_type: str) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "null":
        return value is None
    return False


def _assert_matches_cli_schema(payload: dict[str, Any]) -> None:
    schema = _schema()
    assert isinstance(payload, dict)
    required = schema.get("required", [])
    assert isinstance(required, list)
    for field in required:
        assert field in payload, f"missing required field: {field}"

    props = schema.get("properties", {})
    assert isinstance(props, dict)
    if schema.get("additionalProperties") is False:
        assert set(payload.keys()).issubset(set(props.keys()))

    for name, prop in props.items():
        if name not in payload:
            continue
        assert isinstance(prop, dict)
        expected_type = prop.get("type")
        actual = payload[name]
        if isinstance(expected_type, list):
            assert any(_matches_json_type(actual, item) for item in expected_type)
        elif isinstance(expected_type, str):
            assert _matches_json_type(actual, expected_type)
        if "const" in prop:
            assert actual == prop["const"]
        min_length = prop.get("minLength")
        if isinstance(min_length, int) and isinstance(actual, str):
            assert len(actual) >= min_length

    assert isinstance(payload["issues"], list)
    assert all(isinstance(item, dict) for item in payload["issues"])
    assert isinstance(payload["next_actions"], list)
    assert all(isinstance(item, str) for item in payload["next_actions"])
    assert isinstance(payload["data"], dict)


def test_check_json_envelope_contains_schema_version(tmp_path: Path) -> None:
    LocalOrchestrator(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["check", "--workspace", str(tmp_path), "--output", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    _assert_matches_cli_schema(payload)
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
        [
            "replay",
            run_id,
            "--workspace",
            str(tmp_path),
            "--no-tui",
            "--output",
            "json",
        ],
    )
    assert result.exit_code in {0, 1}
    payload = json.loads(result.stdout)
    _assert_matches_cli_schema(payload)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "replay"
    assert payload["run_id"]


def test_tui_json_requires_dry_run(tmp_path: Path) -> None:
    LocalOrchestrator(tmp_path)
    runner = CliRunner()
    bad = runner.invoke(app, ["tui", "--workspace", str(tmp_path), "--output", "json"])
    assert bad.exit_code != 0
    bad_payload = json.loads(bad.stdout)
    _assert_matches_cli_schema(bad_payload)
    assert bad_payload["schema_version"] == "cli-output.v1"
    assert bad_payload["command"] == "tui"
    assert bad_payload["ok"] is False

    ok = runner.invoke(
        app, ["tui", "--workspace", str(tmp_path), "--output", "json", "--dry-run"]
    )
    assert ok.exit_code == 0
    ok_payload = json.loads(ok.stdout)
    _assert_matches_cli_schema(ok_payload)
    assert ok_payload["schema_version"] == "cli-output.v1"
    assert ok_payload["command"] == "tui"
    assert ok_payload["ok"] is True


def test_json_envelopes_match_schema_for_additional_commands(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    run_id = orch.start_run(plan_content=_plan_content())
    assert orch.wait_for_run(run_id, timeout=8.0)

    runner = CliRunner()
    invocations = [
        ["doctor", "--workspace", str(tmp_path), "--output", "json"],
        ["history", "--workspace", str(tmp_path), "--output", "json"],
        ["validate", "--workspace", str(tmp_path), "--json"],
        ["run", "--workspace", str(tmp_path), "--no-tui", "--yes", "--output", "json"],
        ["recover", "--workspace", str(tmp_path), "--no-tui", "--json"],
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--no-tui",
            "--yes",
            "--output",
            "json",
        ],
    ]
    for args in invocations:
        result = runner.invoke(app, args)
        assert result.exit_code in {0, 1, 10}
        payload = json.loads(result.stdout)
        _assert_matches_cli_schema(payload)


def test_check_quiet_suppresses_subprocess_output(tmp_path: Path) -> None:
    LocalOrchestrator(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", "--workspace", str(tmp_path), "--quiet"])
    assert result.exit_code == 0
    assert "Listing 'packages/engine/src'" not in result.stdout
