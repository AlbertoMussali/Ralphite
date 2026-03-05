from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static

from ralphite_engine.presentation import present_run_status

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class RunsScreen(Vertical):
    DEFAULT_CSS = """
    RunsScreen {
      height: 1fr;
      padding: 1;
    }
    #runs-status {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #runs-controls {
      margin-bottom: 1;
      height: auto;
    }
    #runs-history {
      height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("No active run", id="runs-status")
        with Horizontal(id="runs-controls"):
            yield Button("Start", id="start", variant="success")
            yield Button("Pause", id="pause")
            yield Button("Resume", id="resume")
            yield Button("Cancel", id="cancel", variant="warning")
        history = DataTable(id="runs-history")
        history.add_columns("Run ID", "Status", "Next Action", "Plan", "Completed")
        yield history

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self.set_interval(0.4, self._tick)
        self._refresh_history()
        self._refresh_status()

    def _history_table(self) -> DataTable:
        return self.query_one("#runs-history", DataTable)

    def _status_widget(self) -> Static:
        return self.query_one("#runs-status", Static)

    def _refresh_status(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self._status_widget().update(
                "Ready. Start a run with `s` or command palette."
            )
            return
        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            self._status_widget().update(f"Run {run_id} not found")
            return
        status = present_run_status(run.status)
        active = run.active_node_id or "-"
        self._status_widget().update(
            f"Run {run.id} | status={status.label} | active={active} | retries={run.retry_count}\nNext: {status.next_action}"
        )

    def _refresh_history(self) -> None:
        table = self._history_table()
        table.clear()
        for run in self.shell.orchestrator.list_history(limit=25):
            status = present_run_status(run.status)
            table.add_row(
                run.id[:8],
                status.label,
                status.next_action,
                run.plan_path,
                run.completed_at or "-",
            )

    def _tick(self) -> None:
        self._refresh_status()
        self._refresh_history()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.shell.action_start_run()
        elif event.button.id == "pause":
            self.shell.action_pause_run()
        elif event.button.id == "resume":
            self.shell.action_resume_run()
        elif event.button.id == "cancel":
            self.shell.action_cancel_run()
