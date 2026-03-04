from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from ralphite_engine.presentation import present_run_status
from ralphite_engine.validation import validate_plan_content

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class HomeScreen(Vertical):
    DEFAULT_CSS = """
    HomeScreen {
      padding: 1 2;
      border: round $surface;
      height: 1fr;
    }
    #home-title {
      text-style: bold;
      margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Ralphite TUI Home", id="home-title")
        yield Static("Primary loop: setup run -> phase timeline -> recovery (if needed) -> summary.")
        yield Static("Use top navigation or `ctrl+p`/`:` command palette for every action.")
        yield Static("Task source remains file-based; TUI controls execution structure and run flow.")
        yield Static("", id="home-status")
        with Horizontal():
            yield Button("Create Plan", id="home-create-plan", variant="primary")
            yield Button("Open Run Setup", id="home-open-setup")
            yield Button("Validate Plan", id="home-validate")
            yield Button("Start Run", id="home-start", variant="success")
            yield Button("View Last Failure", id="home-last-failure", variant="warning")

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self._refresh_status()

    def _status(self) -> Static:
        return self.query_one("#home-status", Static)

    def _refresh_status(self) -> None:
        plans = self.shell.orchestrator.list_plans()
        history = self.shell.orchestrator.list_history(limit=20)
        failed = next((run for run in history if run.status == "failed"), None)
        lines = [f"Plans discovered: {len(plans)}"]
        if failed:
            status = present_run_status(failed.status)
            lines.append(f"Last failed run: {failed.id} ({status.label})")
            lines.append(f"Suggested next action: {status.next_action}")
        else:
            lines.append("No failed runs in recent history.")
        self._status().update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "home-create-plan":
            path = self.shell.orchestrator.goal_to_plan("Quickstart objective")
            self._status().update(f"Created plan: {path.name}")
            self._refresh_status()
        elif button_id == "home-open-setup":
            self.shell.show_screen("run_setup")
        elif button_id == "home-validate":
            plans = self.shell.orchestrator.list_plans()
            if not plans:
                self._status().update("No plans available for validation.")
                return
            content = plans[0].read_text(encoding="utf-8")
            valid, issues, _summary = validate_plan_content(content, workspace_root=self.shell.orchestrator.workspace_root)
            if valid:
                self._status().update(f"Validation passed for {plans[0].name}.")
            else:
                first = issues[0] if issues else {}
                self._status().update(
                    f"Validation failed for {plans[0].name}: {first.get('code')} {first.get('message')}"
                )
        elif button_id == "home-start":
            self.shell.action_start_run()
        elif button_id == "home-last-failure":
            history = self.shell.orchestrator.list_history(limit=50)
            failed = next((run for run in history if run.status == "failed"), None)
            if not failed:
                self._status().update("No failed run found.")
                return
            self.shell.current_run_id = failed.id
            self.shell.show_screen("summary")
