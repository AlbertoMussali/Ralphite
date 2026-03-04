from __future__ import annotations

from pathlib import Path

from ralphite_engine import LocalOrchestrator
from ralphite_tui.tui.app_shell import AppShell


def test_palette_contains_core_commands(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    shell = AppShell(orchestrator=orch)
    commands, handlers = shell._command_map()  # noqa: SLF001

    command_ids = {command.id for command in commands}
    assert "nav.editor" in command_ids
    assert "run.start" in command_ids
    assert "migration.strict" in command_ids

    assert "nav.editor" in handlers
    assert "run.start" in handlers


def test_strict_migration_gate_on_shell(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    shell = AppShell(orchestrator=orch)

    ok, messages = shell.run_strict_migration()

    assert ok is True
    assert messages
