from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Input

from ralphite_engine.presentation import present_run_status

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class HistoryScreen(Vertical):
    DEFAULT_CSS = """
    HistoryScreen {
      height: 1fr;
      padding: 1;
    }
    #history-query {
      margin-bottom: 1;
    }
    #history-table {
      height: 1fr;
    }
    """

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter run history", id="history-query")
        table = DataTable(id="history-table")
        table.add_columns("Run ID", "Status", "Next Action", "Plan", "Created", "Completed")
        yield table

    def on_mount(self) -> None:
        self._refresh(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "history-query":
            self._refresh(event.value)

    def _refresh(self, query: str | None) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear()
        rows = self.shell.orchestrator.list_history(limit=50, query=query or None)
        for run in rows:
            status = present_run_status(run.status)
            table.add_row(run.id, status.label, status.next_action, run.plan_path, run.created_at, run.completed_at or "-")
