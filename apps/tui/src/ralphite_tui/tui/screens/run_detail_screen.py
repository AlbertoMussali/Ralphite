from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class RunDetailScreen(Vertical):
    DEFAULT_CSS = """
    RunDetailScreen {
      height: 1fr;
      padding: 1;
    }
    #run-detail-summary {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #run-detail-events {
      height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._seen_ids: set[int] = set()

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("No active run selected.", id="run-detail-summary")
        events = DataTable(id="run-detail-events")
        events.add_columns("#", "Event", "Level", "Message")
        yield events

    def on_mount(self) -> None:
        self.set_interval(0.25, self._tick)

    def _summary(self) -> Static:
        return self.query_one("#run-detail-summary", Static)

    def _events(self) -> DataTable:
        return self.query_one("#run-detail-events", DataTable)

    def _tick(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self._summary().update("No active run selected.")
            return

        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            self._summary().update(f"Run {run_id} not found")
            return

        self._summary().update(
            f"Run {run.id} | status={run.status} | active={run.active_node_id or '-'} | retries={run.retry_count}"
        )

        table = self._events()
        for event in self.shell.orchestrator.poll_events(run_id):
            event_id = int(event.get("id", 0))
            if event_id in self._seen_ids:
                continue
            self._seen_ids.add(event_id)
            table.add_row(str(event_id), event.get("event", ""), event.get("level", ""), event.get("message", ""))

        # Backfill from persisted events so screen works after restart/recover.
        if not table.row_count:
            for event in self.shell.orchestrator.stream_events(run_id):
                event_id = int(event.get("id", 0))
                if event_id in self._seen_ids:
                    continue
                self._seen_ids.add(event_id)
                table.add_row(str(event_id), event.get("event", ""), event.get("level", ""), event.get("message", ""))
