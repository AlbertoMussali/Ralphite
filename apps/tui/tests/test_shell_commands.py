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


def test_shell_has_navigation_shortcut_bindings() -> None:
    bindings = {(key, action) for (key, action, _) in AppShell.BINDINGS}

    assert ("ctrl+p", "command_palette") in bindings
    assert (":", "command_palette") in bindings
    assert ("1", "nav_home") in bindings
    assert ("2", "nav_run_setup") in bindings
    assert ("3", "nav_runs") in bindings
    assert ("4", "nav_phase_timeline") in bindings
    assert ("5", "nav_recovery") in bindings
    assert ("6", "nav_summary") in bindings
    assert ("7", "nav_history") in bindings
    assert ("8", "nav_settings") in bindings


def test_shell_keeps_textual_registry_intact(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    shell = AppShell(orchestrator=orch)

    # Textual's App internals rely on _registry behaving like a set.
    assert hasattr(shell._registry, "add")
    assert isinstance(shell._palette_handlers, dict)


def test_open_palette_alias_targets_command_palette(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    shell = AppShell(orchestrator=orch)
    called = {"value": False}

    def _mark_called() -> None:
        called["value"] = True

    shell.action_command_palette = _mark_called  # type: ignore[method-assign]
    shell.action_open_palette()
    assert called["value"] is True


def test_shell_can_start_run_for_plan(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    shell = AppShell(orchestrator=orch)
    plans = orch.list_plans()

    assert plans
    run_id = shell.start_run_for_plan(str(plans[0]))
    assert run_id is not None
    assert orch.wait_for_run(run_id, timeout=8.0) is True
