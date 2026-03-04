from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class RecoveryScreen(Vertical):
    DEFAULT_CSS = """
    RecoveryScreen {
      height: 1fr;
      padding: 1;
    }
    #recovery-status {
      border: round $warning;
      padding: 1;
      margin-bottom: 1;
    }
    #recovery-controls {
      height: auto;
      margin-top: 1;
    }
    """

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Recovery console", id="recovery-status")
        yield Input(placeholder="Best-effort recovery prompt (optional)", id="recovery-prompt")
        with Horizontal(id="recovery-controls"):
            yield Button("Manual", id="mode-manual")
            yield Button("Best Effort Agent", id="mode-agent", variant="warning")
            yield Button("Abort Phase", id="mode-abort", variant="error")
            yield Button("Resume", id="resume", variant="success")

    def on_mount(self) -> None:
        self.set_interval(0.4, self._refresh)

    def _status(self) -> Static:
        return self.query_one("#recovery-status", Static)

    def _prompt(self) -> str | None:
        value = self.query_one("#recovery-prompt", Input).value.strip()
        return value or None

    def _refresh(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self._status().update("No active run selected.")
            return
        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            self._status().update(f"Run {run_id} not found")
            return

        recovery = run.metadata.get("recovery", {})
        details = recovery.get("details", {}) if isinstance(recovery, dict) else {}
        self._status().update(
            f"Run {run.id} | status={run.status} | recovery={recovery.get('status', 'none')} | "
            f"mode={recovery.get('selected_mode') or '-'}\n"
            f"details: {details}"
        )

    def _set_mode(self, mode: str) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            return
        ok = self.shell.orchestrator.set_recovery_mode(run_id, mode, prompt=self._prompt())
        if ok:
            self._status().update(f"Recovery mode set: {mode}")
        else:
            self._status().update(f"Failed to set recovery mode: {mode}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "mode-manual":
            self._set_mode("manual")
        elif button_id == "mode-agent":
            self._set_mode("agent_best_effort")
        elif button_id == "mode-abort":
            self._set_mode("abort_phase")
        elif button_id == "resume":
            run_id = self.shell.current_run_id
            if not run_id:
                return
            resumed = self.shell.orchestrator.resume_from_checkpoint(run_id)
            if resumed:
                self._status().update("Recovery resumed.")
                self.shell.show_screen("phase_timeline")
            else:
                self._status().update("Resume failed. Select recovery mode first.")
