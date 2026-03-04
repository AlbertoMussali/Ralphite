from __future__ import annotations

from pathlib import Path

from ralphite_engine import LocalOrchestrator
from ralphite_tui.tui.app_shell import AppShell


def test_palette_contains_core_commands(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    shell = AppShell(orchestrator=orch)
    commands, handlers = shell._command_map()  # noqa: SLF001

    command_ids = {command.id for command in commands}
    assert "nav.run_setup" in command_ids
    assert "run.start" in command_ids
    assert "recovery.agent" in command_ids

    assert "nav.run_setup" in handlers
    assert "run.start" in handlers


def test_shell_can_start_run_for_plan(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    shell = AppShell(orchestrator=orch)
    plans = orch.list_plans()

    assert plans
    run_id = shell.start_run_for_plan(str(plans[0]))
    assert run_id is not None
    assert orch.wait_for_run(run_id, timeout=8.0) is True
