from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

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
    """

    def __init__(self) -> None:
        super().__init__()
        self._seen_ids: set[int] = set()

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("No active run selected.", id="phase-summary")
        yield Static("No phase progress yet.", id="phase-progress")
        yield Static("No failures.", id="phase-failures")
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
        self._summary().update(
            f"Run {run.id} | status={run.status} | active={run.active_node_id or '-'} | "
            f"phase_done={len(done_phases)} | recovery={recovery.get('status', 'none')}"
        )
        self._progress().update(self._build_phase_progress(run))
        self._failures().update(self._build_failure_summary(run))

        table = self._events()
        for event in self.shell.orchestrator.poll_events(run_id):
            event_id = int(event.get("id", 0))
            if event_id in self._seen_ids:
                continue
            self._seen_ids.add(event_id)
            meta = event.get("meta") or {}
            lane = meta.get("lane") if isinstance(meta, dict) else ""
            table.add_row(
                str(event_id),
                event.get("event", ""),
                event.get("group", "") or "",
                str(lane or ""),
                event.get("level", ""),
                event.get("message", ""),
            )

        if not table.row_count:
            for event in self.shell.orchestrator.stream_events(run_id):
                event_id = int(event.get("id", 0))
                if event_id in self._seen_ids:
                    continue
                self._seen_ids.add(event_id)
                meta = event.get("meta") or {}
                lane = meta.get("lane") if isinstance(meta, dict) else ""
                table.add_row(
                    str(event_id),
                    event.get("event", ""),
                    event.get("group", "") or "",
                    str(lane or ""),
                    event.get("level", ""),
                    event.get("message", ""),
                )
