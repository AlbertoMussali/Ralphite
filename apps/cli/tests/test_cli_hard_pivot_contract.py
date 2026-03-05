from __future__ import annotations

import json
from pathlib import Path
import subprocess

from typer.testing import CliRunner

import ralphite_cli.commands.check_cmd as check_mod
from ralphite_cli.checks.suites import STRICT_SUITES
from ralphite_cli.cli import app


def test_tui_command_removed(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["tui", "--workspace", str(tmp_path)])
    assert result.exit_code == 2


def test_no_tui_flag_removed(tmp_path: Path) -> None:
    runner = CliRunner()
    run_result = runner.invoke(
        app,
        ["run", "--workspace", str(tmp_path), "--no-tui", "--yes", "--output", "json"],
    )
    assert run_result.exit_code == 2

    quickstart_result = runner.invoke(
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
    assert quickstart_result.exit_code == 2


def test_strict_suites_have_no_tui_paths() -> None:
    for _name, command in STRICT_SUITES:
        joined = " ".join(command)
        assert "apps/cli" in joined or "packages/engine" in joined


def test_check_full_targets_cli_tests(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[list[str]] = []

    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [],
            "plan_failures": [],
            "stale_artifacts": {},
            "fix_suggestions": [],
        }

    def fake_run(command, cwd, check, capture_output, text):  # noqa: ANN001
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(check_mod, "_doctor_snapshot", fake_snapshot)
    monkeypatch.setattr(check_mod.subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--full", "--output", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    joined = [" ".join(row) for row in seen]
    assert any("apps/cli/tests" in row for row in joined)


def test_doctor_fix_suggestions_payload(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "broken.yaml").write_text(
        "version: 4\nplan_id: bad\nname: bad\n", encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "doctor",
            "--workspace",
            str(tmp_path),
            "--fix-suggestions",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert isinstance(payload["data"].get("fix_suggestions"), list)
