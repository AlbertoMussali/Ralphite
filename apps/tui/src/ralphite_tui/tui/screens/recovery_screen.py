from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
      margin-bottom: 1;
    }
    #recovery-guidance {
      border: round $accent;
      padding: 1;
      height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._last_preflight: dict[str, Any] | None = None

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
            yield Button("Preflight", id="preflight")
            yield Button("Resume", id="resume", variant="success")
        yield Static("No recovery guidance yet.", id="recovery-guidance")

    def on_mount(self) -> None:
        self.set_interval(0.4, self._refresh)

    def _status(self) -> Static:
        return self.query_one("#recovery-status", Static)

    def _guidance(self) -> Static:
        return self.query_one("#recovery-guidance", Static)

    def _prompt(self) -> str | None:
        value = self.query_one("#recovery-prompt", Input).value.strip()
        return value or None

    def _render_preflight(self, preflight: dict[str, Any]) -> None:
        checks = preflight.get("checks", []) if isinstance(preflight.get("checks"), list) else []
        blocking = preflight.get("blocking_reasons", []) if isinstance(preflight.get("blocking_reasons"), list) else []
        conflict_files = preflight.get("conflict_files", []) if isinstance(preflight.get("conflict_files"), list) else []
        unresolved = preflight.get("unresolved_conflict_files", []) if isinstance(preflight.get("unresolved_conflict_files"), list) else []
        next_commands = preflight.get("next_commands", []) if isinstance(preflight.get("next_commands"), list) else []

        lines = [f"Preflight: {'PASS' if preflight.get('ok') else 'FAIL'}", "", "Checks:"]
        lines.extend([f"- {'OK' if check.get('ok') else 'FAIL'} {check.get('name')}: {check.get('detail')}" for check in checks])
        lines.append("")
        lines.append("Conflict files:")
        lines.extend([f"- {item}" for item in conflict_files] or ["- none"])
        lines.append("")
        lines.append("Unresolved conflict files:")
        lines.extend([f"- {item}" for item in unresolved] or ["- none"])
        lines.append("")
        lines.append("Next commands:")
        lines.extend([f"- {item}" for item in next_commands] or ["- none"])
        lines.append("")
        lines.append("Blocking reasons:")
        lines.extend([f"- {item}" for item in blocking] or ["- none"])

        self._guidance().update("\n".join(lines))

    def _refresh(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self._status().update("No active run selected.")
            return
        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            self._status().update(f"Run {run_id} not found")
            return

        recovery = run.metadata.get("recovery", {}) if isinstance(run.metadata.get("recovery"), dict) else {}
        details = recovery.get("details", {}) if isinstance(recovery.get("details"), dict) else {}
        conflict_files = details.get("conflict_files", []) if isinstance(details.get("conflict_files"), list) else []
        self._status().update(
            f"Run {run.id} | status={run.status} | recovery={recovery.get('status', 'none')} | "
            f"mode={recovery.get('selected_mode') or '-'} | conflicts={len(conflict_files)}"
        )

        if self._last_preflight is None and run.status == "paused_recovery_required":
            self._last_preflight = self.shell.orchestrator.recovery_preflight(run_id)
            self._render_preflight(self._last_preflight)

    def _set_mode(self, mode: str) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            return
        ok = self.shell.orchestrator.set_recovery_mode(run_id, mode, prompt=self._prompt())
        if ok:
            self._status().update(f"Recovery mode set: {mode}")
            self._last_preflight = self.shell.orchestrator.recovery_preflight(run_id)
            self._render_preflight(self._last_preflight)
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
        elif button_id == "preflight":
            run_id = self.shell.current_run_id
            if not run_id:
                return
            self._last_preflight = self.shell.orchestrator.recovery_preflight(run_id)
            self._render_preflight(self._last_preflight)
        elif button_id == "resume":
            run_id = self.shell.current_run_id
            if not run_id:
                return
            preflight = self.shell.orchestrator.recovery_preflight(run_id)
            self._last_preflight = preflight
            self._render_preflight(preflight)
            if not preflight.get("ok"):
                self._status().update("Resume blocked by preflight failures.")
                return
            resumed = self.shell.orchestrator.resume_from_checkpoint(run_id)
            if resumed:
                self._status().update("Recovery resumed.")
                self.shell.show_screen("phase_timeline")
            else:
                self._status().update("Resume failed. Check preflight output and selected mode.")
