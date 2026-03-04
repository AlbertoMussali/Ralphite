from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class ArtifactsScreen(Vertical):
    DEFAULT_CSS = """
    ArtifactsScreen {
      height: 1fr;
      padding: 1;
    }
    #artifacts-status {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #artifacts-table {
      height: 1fr;
    }
    """

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Artifacts for active run", id="artifacts-status")
        table = DataTable(id="artifacts-table")
        table.add_columns("ID", "Format", "Path")
        yield table

    def on_mount(self) -> None:
        self.set_interval(0.75, self._refresh)

    def _refresh(self) -> None:
        status = self.query_one("#artifacts-status", Static)
        table = self.query_one("#artifacts-table", DataTable)
        table.clear()

        run_id = self.shell.current_run_id
        if not run_id:
            status.update("No active run selected.")
            return

        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            status.update(f"Run {run_id} not found")
            return

        status.update(f"Run {run.id} | status={run.status} | artifacts={len(run.artifacts)}")
        for artifact in run.artifacts:
            table.add_row(artifact.get("id", ""), artifact.get("format", ""), artifact.get("path", ""))
