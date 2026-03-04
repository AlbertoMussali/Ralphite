from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class SummaryScreen(Vertical):
    DEFAULT_CSS = """
    SummaryScreen {
      height: 1fr;
      padding: 1;
    }
    #summary-status {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #summary-sections {
      border: round $surface;
      padding: 1;
      margin-bottom: 1;
      height: auto;
    }
    #summary-artifacts {
      height: 1fr;
    }
    """

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Post-run summary", id="summary-status")
        yield Static("No summary sections yet.", id="summary-sections")
        table = DataTable(id="summary-artifacts")
        table.add_columns("ID", "Format", "Path")
        yield table

    def on_mount(self) -> None:
        self.set_interval(0.75, self._refresh)

    def _build_sections(self, run) -> str:
        git_state = run.metadata.get("git_state", {}) if isinstance(run.metadata.get("git_state"), dict) else {}
        phase_states = git_state.get("phases", {}) if isinstance(git_state.get("phases"), dict) else {}
        recovery = run.metadata.get("recovery", {}) if isinstance(run.metadata.get("recovery"), dict) else {}

        integration_lines = []
        for phase, data in phase_states.items():
            if not isinstance(data, dict):
                continue
            integration_lines.append(
                f"- {phase}: integrated_to_base={bool(data.get('integrated_to_base'))} merged_workers={len(data.get('merged_workers', []))}"
            )

        cleanup_items: list[str] = []
        for evt in run.events:
            if evt.get("event") != "CLEANUP_DONE":
                continue
            meta = evt.get("meta") if isinstance(evt.get("meta"), dict) else {}
            items = meta.get("items") if isinstance(meta.get("items"), list) else []
            cleanup_items.extend(str(item) for item in items)

        warnings: list[str] = []
        details = recovery.get("details", {}) if isinstance(recovery.get("details"), dict) else {}
        conflict_files = details.get("conflict_files") if isinstance(details.get("conflict_files"), list) else []
        if conflict_files:
            warnings.extend([f"unresolved conflict file reported: {item}" for item in conflict_files])

        recovery_history = [
            f"- [{evt.get('level')}] {evt.get('event')}: {evt.get('message')}"
            for evt in run.events
            if str(evt.get("event", "")).startswith("RECOVERY_")
        ]

        lines = [
            "Integration Results:",
            *(integration_lines or ["- none"]),
            "",
            "Cleanup Results:",
            *([f"- {item}" for item in cleanup_items] or ["- none"]),
            "",
            "Unresolved Warnings:",
            *([f"- {item}" for item in warnings] or ["- none"]),
            "",
            "Recovery History:",
            *(recovery_history or ["- none"]),
        ]
        return "\n".join(lines)

    def _refresh(self) -> None:
        status = self.query_one("#summary-status", Static)
        sections = self.query_one("#summary-sections", Static)
        table = self.query_one("#summary-artifacts", DataTable)
        table.clear()

        run_id = self.shell.current_run_id
        if not run_id:
            status.update("No active run selected.")
            return

        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            status.update(f"Run {run_id} not found")
            return

        done_phases = run.metadata.get("phase_done", run.metadata.get("v2_phase_done", []))
        cleanup = [evt for evt in run.events if evt.get("event") == "CLEANUP_DONE"]
        status.update(
            f"Run {run.id} | status={run.status} | phases_done={len(done_phases)} | cleanup_events={len(cleanup)}"
        )
        sections.update(self._build_sections(run))
        for artifact in run.artifacts:
            table.add_row(artifact.get("id", ""), artifact.get("format", ""), artifact.get("path", ""))
