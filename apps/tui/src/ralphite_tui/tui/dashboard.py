from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Static

from ralphite_engine import LocalOrchestrator


class DashboardApp(App[None]):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "start_run", "Start Run"),
        ("p", "pause_run", "Pause"),
        ("r", "resume_run", "Resume"),
        ("c", "cancel_run", "Cancel"),
    ]

    CSS = """
    Screen {
      layout: vertical;
    }

    #status {
      padding: 1;
      border: round $accent;
      margin: 1 1 0 1;
    }

    #controls {
      height: auto;
      margin: 0 1;
    }

    DataTable {
      height: 1fr;
      margin: 1;
      border: round $surface;
    }

    #events {
      height: 2fr;
    }

    #history {
      height: 1fr;
    }
    """

    def __init__(self, orchestrator: LocalOrchestrator, run_id: str | None = None) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.current_run_id = run_id
        self._event_keys: set[int] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Ralphite TUI Dashboard", id="status")
        with Horizontal(id="controls"):
            yield Button("Start Run", id="start", variant="success")
            yield Button("Pause", id="pause")
            yield Button("Resume", id="resume")
            yield Button("Cancel", id="cancel", variant="warning")
            yield Button("Quit", id="quit", variant="error")
        with Vertical():
            history = DataTable(id="history")
            history.add_columns("Run ID", "Status", "Plan", "Completed")
            yield history
            events = DataTable(id="events")
            events.add_columns("#", "Event", "Level", "Message")
            yield events
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.2, self._tick)
        self._refresh_history()
        self._refresh_status()

    def _status_widget(self) -> Static:
        return self.query_one("#status", Static)

    def _history_table(self) -> DataTable:
        return self.query_one("#history", DataTable)

    def _events_table(self) -> DataTable:
        return self.query_one("#events", DataTable)

    def _refresh_status(self) -> None:
        if not self.current_run_id:
            self._status_widget().update("Ready. Press Start Run (`s`) to execute latest plan.")
            return
        run = self.orchestrator.get_run(self.current_run_id)
        if not run:
            self._status_widget().update(f"Run {self.current_run_id} not found")
            return
        active = run.active_node_id or "-"
        self._status_widget().update(
            f"Run {run.id} | status={run.status} | active={active} | retries={run.retry_count}"
        )

    def _refresh_history(self) -> None:
        table = self._history_table()
        table.clear()
        for run in self.orchestrator.list_history(limit=20):
            table.add_row(run.id[:8], run.status, run.plan_path, run.completed_at or "-")

    def _append_events(self) -> None:
        if not self.current_run_id:
            return
        table = self._events_table()
        for event in self.orchestrator.poll_events(self.current_run_id):
            event_id = int(event.get("id", 0))
            if event_id in self._event_keys:
                continue
            self._event_keys.add(event_id)
            table.add_row(str(event_id), event["event"], event["level"], event["message"])
            if event["event"] == "RUN_DONE":
                self._refresh_history()

    def _tick(self) -> None:
        self._append_events()
        self._refresh_status()

    def _start_run(self) -> None:
        run_id = self.orchestrator.start_run()
        self.current_run_id = run_id
        self._event_keys.clear()
        self._events_table().clear()
        self._refresh_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "start":
            self.action_start_run()
        elif button_id == "pause":
            self.action_pause_run()
        elif button_id == "resume":
            self.action_resume_run()
        elif button_id == "cancel":
            self.action_cancel_run()
        elif button_id == "quit":
            self.action_quit()

    def action_start_run(self) -> None:
        self._start_run()

    def action_pause_run(self) -> None:
        if self.current_run_id:
            self.orchestrator.pause_run(self.current_run_id)
            self._refresh_status()

    def action_resume_run(self) -> None:
        if self.current_run_id:
            self.orchestrator.resume_run(self.current_run_id)
            self._refresh_status()

    def action_cancel_run(self) -> None:
        if self.current_run_id:
            self.orchestrator.cancel_run(self.current_run_id)
            self._refresh_status()
