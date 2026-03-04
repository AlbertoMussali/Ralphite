from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static

from ralphite_engine.validation import validate_plan_content

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class RunSetupScreen(Vertical):
    DEFAULT_CSS = """
    RunSetupScreen {
      height: 1fr;
      padding: 1;
    }
    #setup-status {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #setup-controls {
      height: auto;
      margin-bottom: 1;
    }
    #setup-plans {
      height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._plans: list[Path] = []

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Select a v2 plan and start run.", id="setup-status")
        with Horizontal(id="setup-controls"):
            yield Button("Refresh", id="refresh")
            yield Button("Start Selected", id="start-selected", variant="success")
            yield Button("Open Runs", id="open-runs")
        table = DataTable(id="setup-plans")
        table.add_columns("Plan", "Valid", "Phases", "Task Source")
        yield table

    def on_mount(self) -> None:
        self._refresh()

    def _table(self) -> DataTable:
        return self.query_one("#setup-plans", DataTable)

    def _status(self) -> Static:
        return self.query_one("#setup-status", Static)

    def _refresh(self) -> None:
        self._plans = self.shell.orchestrator.list_plans()
        table = self._table()
        table.clear()

        if not self._plans:
            self._status().update("No plans found under .ralphite/plans")
            return

        for plan_path in self._plans:
            valid, _issues, summary = validate_plan_content(
                plan_path.read_text(encoding="utf-8"),
                workspace_root=self.shell.orchestrator.workspace_root,
            )
            task_source = str(summary.get("task_source_status", {}).get("path", "-"))
            table.add_row(plan_path.name, "yes" if valid else "no", str(summary.get("phases", "-")), task_source)

        table.move_cursor(row=0, column=0)
        self._status().update(f"{len(self._plans)} plan(s) ready. Start with selected row.")

    def _selected_plan(self) -> str | None:
        table = self._table()
        row_index = table.cursor_row
        if row_index is None or row_index < 0 or row_index >= len(self._plans):
            return None
        return str(self._plans[row_index])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self._refresh()
            return
        if event.button.id == "open-runs":
            self.shell.show_screen("runs")
            return
        if event.button.id == "start-selected":
            selected = self._selected_plan()
            if not selected:
                self._status().update("No plan selected.")
                return
            run_id = self.shell.start_run_for_plan(selected)
            if not run_id:
                self._status().update("Unable to start run for selected plan.")
                return
            self._status().update(f"Started run {run_id}")
            self.shell.show_screen("phase_timeline")
