from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from ralphite_engine.presentation import present_event, present_run_status

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class PhaseTimelineScreen(Vertical):
    DEFAULT_CSS = """
    PhaseTimelineScreen {
      height: 1fr;
      padding: 1;
    }
    #phase-summary {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #phase-progress {
      border: round $surface;
      padding: 1;
      margin-bottom: 1;
      height: auto;
    }
    #phase-failures {
      border: round $warning;
      padding: 1;
      margin-bottom: 1;
      height: auto;
    }
    #phase-events {
      height: 1fr;
    }
    #phase-controls {
      height: auto;
      margin-bottom: 1;
    }
    .phase-filter {
      width: 1fr;
    }
    #phase-page-meta {
      margin-left: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._seen_ids: set[int] = set()
        self._event_rows: list[dict[str, str]] = []
        self._errors_only = False
        self._autoscroll = True
        self._compact = False
        self._failures_with_next_action = False
        self._retention_options = [200, 500, 1000]
        self._retention_index = 1
        self._page_size = 50
        self._page_index = 0

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("No active run selected.", id="phase-summary")
        yield Static("No phase progress yet.", id="phase-progress")
        yield Static("No failures.", id="phase-failures")
        with Horizontal(id="phase-controls"):
            yield Button("Errors: Off", id="toggle-errors")
            yield Button("Failures+Next: Off", id="toggle-failure-next")
            yield Button("Autoscroll: On", id="toggle-autoscroll")
            yield Button("Compact: Off", id="toggle-compact")
            yield Button("Retention: 500", id="cycle-retention")
            yield Button("Prev Page", id="page-prev")
            yield Button("Next Page", id="page-next")
            yield Button("Last Page", id="page-last")
            yield Input(placeholder="phase filter", id="filter-phase", classes="phase-filter")
            yield Input(placeholder="lane filter", id="filter-lane", classes="phase-filter")
            yield Input(placeholder="task filter", id="filter-task", classes="phase-filter")
            yield Input(placeholder="event types (comma-separated)", id="filter-event-types", classes="phase-filter")
            yield Static("page 1/1", id="phase-page-meta")
        events = DataTable(id="phase-events")
        events.add_columns("#", "Event", "Phase", "Lane", "Level", "Message")
        yield events

    def on_mount(self) -> None:
        self.set_interval(0.25, self._tick)

    def _summary(self) -> Static:
        return self.query_one("#phase-summary", Static)

    def _progress(self) -> Static:
        return self.query_one("#phase-progress", Static)

    def _failures(self) -> Static:
        return self.query_one("#phase-failures", Static)

    def _events(self) -> DataTable:
        return self.query_one("#phase-events", DataTable)

    def _filter_value(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value.strip().lower()

    def _retention_limit(self) -> int:
        return int(self._retention_options[self._retention_index])

    def _event_type_tokens(self) -> list[str]:
        raw = self._filter_value("filter-event-types")
        if not raw:
            return []
        return [token.strip() for token in raw.split(",") if token.strip()]

    def _filtered_rows(self) -> list[dict[str, str]]:
        phase_filter = self._filter_value("filter-phase")
        lane_filter = self._filter_value("filter-lane")
        task_filter = self._filter_value("filter-task")
        event_tokens = self._event_type_tokens()
        rows: list[dict[str, str]] = []
        for row in self._event_rows:
            phase = row.get("phase", "").lower()
            lane = row.get("lane", "").lower()
            event_name = row.get("event", "").lower()
            raw_event = row.get("raw_event", "").lower()
            task_id = row.get("task_id", "").lower()
            level = row.get("level", "").lower()
            message = row.get("message", "")
            next_action = row.get("next_action", "")
            if self._errors_only and level not in {"error", "warn"}:
                continue
            if self._failures_with_next_action and (level not in {"error", "warn"} or not next_action):
                continue
            if phase_filter and phase_filter not in phase:
                continue
            if lane_filter and lane_filter not in lane:
                continue
            if task_filter and task_filter not in event_name and task_filter not in task_id and task_filter not in message.lower():
                continue
            if event_tokens and not any(token in event_name or token in raw_event for token in event_tokens):
                continue
            rows.append(row)
        return rows

    def _refresh_event_table(self) -> None:
        table = self._events()
        table.clear()
        filtered = self._filtered_rows()
        total = len(filtered)
        total_pages = max(1, ((total - 1) // self._page_size) + 1) if total else 1
        if self._autoscroll:
            self._page_index = total_pages - 1
        else:
            self._page_index = max(0, min(self._page_index, total_pages - 1))

        start = self._page_index * self._page_size
        end = start + self._page_size
        for row in filtered[start:end]:
            message = row.get("message", "")
            display_message = message if not self._compact else (message[:72] + "..." if len(message) > 75 else message)
            table.add_row(
                row.get("id", ""),
                row.get("event", ""),
                row.get("phase", ""),
                row.get("lane", ""),
                row.get("level", ""),
                display_message,
            )
        meta = self.query_one("#phase-page-meta", Static)
        meta.update(f"page {self._page_index + 1}/{total_pages} | rows {table.row_count}/{total} | retain {self._retention_limit()}")
        if self._autoscroll and table.row_count > 0 and self._page_index == total_pages - 1:
            table.move_cursor(row=table.row_count - 1, column=0)

    def _build_phase_progress(self, run) -> str:
        phase_nodes = (
            run.metadata.get("phase_nodes", {})
            if isinstance(run.metadata.get("phase_nodes"), dict)
            else run.metadata.get("v2_phase_nodes", {})
            if isinstance(run.metadata.get("v2_phase_nodes"), dict)
            else {}
        )
        phase_groups = (
            run.metadata.get("phase_parallel_groups", {})
            if isinstance(run.metadata.get("phase_parallel_groups"), dict)
            else {}
        )
        if not phase_nodes:
            return "No phase metadata available."

        lines = ["Phase Progress:"]
        for phase, node_ids in phase_nodes.items():
            counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "blocked": 0}
            total = 0
            for node_id in node_ids:
                node = run.nodes.get(node_id)
                if not node:
                    continue
                total += 1
                counts[node.status] = counts.get(node.status, 0) + 1
            completed = counts.get("succeeded", 0) + counts.get("failed", 0) + counts.get("blocked", 0)
            pct = int((completed / total) * 100) if total else 0
            lines.append(
                f"- {phase}: {pct}% ({completed}/{total}) "
                f"q={counts.get('queued', 0)} r={counts.get('running', 0)} "
                f"ok={counts.get('succeeded', 0)} fail={counts.get('failed', 0)} blocked={counts.get('blocked', 0)}"
            )
            group_rows = phase_groups.get(phase, [])
            if isinstance(group_rows, list) and group_rows:
                for group in group_rows:
                    if not isinstance(group, dict):
                        continue
                    group_id = group.get("group_id", "?")
                    group_nodes = group.get("node_ids", []) if isinstance(group.get("node_ids"), list) else []
                    group_total = len(group_nodes)
                    group_done = 0
                    for node_id in group_nodes:
                        state = run.nodes.get(str(node_id))
                        if state and state.status in {"succeeded", "failed", "blocked"}:
                            group_done += 1
                    lines.append(f"  group {group_id}: {group_done}/{group_total} done")
        return "\n".join(lines)

    def _build_failure_summary(self, run) -> str:
        failures: list[str] = []
        for node_id, node in run.nodes.items():
            if node.status != "failed":
                continue
            result = node.result or {}
            reason = result.get("reason", "unknown") if isinstance(result, dict) else "unknown"
            next_action = result.get("next_action", "inspect timeline") if isinstance(result, dict) else "inspect timeline"
            failures.append(f"- {node_id}: reason={reason}; next={next_action}")
        if not failures:
            return "No failures."
        return "Failure Summary:\n" + "\n".join(failures[:8])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "toggle-errors":
            self._errors_only = not self._errors_only
            event.button.label = f"Errors: {'On' if self._errors_only else 'Off'}"
            self._refresh_event_table()
        elif button_id == "toggle-failure-next":
            self._failures_with_next_action = not self._failures_with_next_action
            event.button.label = f"Failures+Next: {'On' if self._failures_with_next_action else 'Off'}"
            self._refresh_event_table()
        elif button_id == "toggle-autoscroll":
            self._autoscroll = not self._autoscroll
            event.button.label = f"Autoscroll: {'On' if self._autoscroll else 'Off'}"
            self._refresh_event_table()
        elif button_id == "toggle-compact":
            self._compact = not self._compact
            event.button.label = f"Compact: {'On' if self._compact else 'Off'}"
            self._refresh_event_table()
        elif button_id == "cycle-retention":
            self._retention_index = (self._retention_index + 1) % len(self._retention_options)
            event.button.label = f"Retention: {self._retention_limit()}"
            if len(self._event_rows) > self._retention_limit():
                self._event_rows = self._event_rows[-self._retention_limit() :]
            self._refresh_event_table()
        elif button_id == "page-prev":
            self._autoscroll = False
            self._page_index = max(0, self._page_index - 1)
            self._refresh_event_table()
        elif button_id == "page-next":
            self._autoscroll = False
            self._page_index += 1
            self._refresh_event_table()
        elif button_id == "page-last":
            self._autoscroll = True
            self._refresh_event_table()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"filter-phase", "filter-lane", "filter-task", "filter-event-types"}:
            self._refresh_event_table()

    def _tick(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self._summary().update("No active run selected.")
            return

        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            self._summary().update(f"Run {run_id} not found")
            return

        done_phases = run.metadata.get("phase_done", run.metadata.get("v2_phase_done", []))
        recovery = run.metadata.get("recovery", {})
        status = present_run_status(run.status)
        nodes = list(run.nodes.values())
        total = len(nodes)
        succeeded = len([node for node in nodes if node.status == "succeeded"])
        failed = len([node for node in nodes if node.status == "failed"])
        blocked = len([node for node in nodes if node.status == "blocked"])
        success_pct = int((succeeded / total) * 100) if total else 0
        self._summary().update(
            f"Run {run.id} | status={status.label} | active={run.active_node_id or '-'} | "
            f"phase_done={len(done_phases)} | recovery={recovery.get('status', 'none')}\n"
            f"Health: success={success_pct}% ({succeeded}/{total}) failed={failed} blocked={blocked}\n"
            f"Next: {status.next_action}"
        )
        self._progress().update(self._build_phase_progress(run))
        self._failures().update(self._build_failure_summary(run))

        for event in self.shell.orchestrator.poll_events(run_id):
            event_id = int(event.get("id", 0))
            if event_id in self._seen_ids:
                continue
            self._seen_ids.add(event_id)
            meta = event.get("meta") or {}
            lane = meta.get("lane") if isinstance(meta, dict) else ""
            raw_event = str(event.get("event", ""))
            info = present_event(raw_event)
            self._event_rows.append(
                {
                    "id": str(event_id),
                    "event": info.title,
                    "raw_event": raw_event,
                    "phase": str(event.get("group", "") or ""),
                    "lane": str(lane or ""),
                    "level": str(event.get("level", "")),
                    "message": str(event.get("message", "")),
                    "task_id": str(event.get("task_id", "")),
                    "next_action": info.next_action,
                }
            )
            if len(self._event_rows) > self._retention_limit():
                self._event_rows = self._event_rows[-self._retention_limit() :]

        if not self._event_rows:
            for event in self.shell.orchestrator.stream_events(run_id):
                event_id = int(event.get("id", 0))
                if event_id in self._seen_ids:
                    continue
                self._seen_ids.add(event_id)
                meta = event.get("meta") or {}
                lane = meta.get("lane") if isinstance(meta, dict) else ""
                raw_event = str(event.get("event", ""))
                info = present_event(raw_event)
                self._event_rows.append(
                    {
                        "id": str(event_id),
                        "event": info.title,
                        "raw_event": raw_event,
                        "phase": str(event.get("group", "") or ""),
                        "lane": str(lane or ""),
                        "level": str(event.get("level", "")),
                        "message": str(event.get("message", "")),
                        "task_id": str(event.get("task_id", "")),
                        "next_action": info.next_action,
                    }
                )
                if len(self._event_rows) > self._retention_limit():
                    self._event_rows = self._event_rows[-self._retention_limit() :]
        self._refresh_event_table()
