from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

from ralphite_engine.presentation import present_recovery_mode, present_run_status
from ralphite_tui.tui.system_actions import copy_text_to_clipboard, open_local_path

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
        yield Static(
            "Step 1: Select mode. Step 2: Run preflight. Step 3: Resume.",
            id="recovery-step",
        )
        yield Input(
            placeholder="Best-effort recovery prompt (optional)", id="recovery-prompt"
        )
        with Horizontal(id="recovery-controls"):
            yield Button("Manual", id="mode-manual")
            yield Button("Best Effort Agent", id="mode-agent", variant="warning")
            yield Button("Abort Phase", id="mode-abort", variant="error")
            yield Button("Preflight", id="preflight")
            yield Button("Resume", id="resume", variant="success")
            yield Button("Show Worktree", id="show-worktree")
            yield Button("Show Commands", id="show-commands")
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
        checks = (
            preflight.get("checks", [])
            if isinstance(preflight.get("checks"), list)
            else []
        )
        blocking = (
            preflight.get("blocking_reasons", [])
            if isinstance(preflight.get("blocking_reasons"), list)
            else []
        )
        conflict_files = (
            preflight.get("conflict_files", [])
            if isinstance(preflight.get("conflict_files"), list)
            else []
        )
        unresolved = (
            preflight.get("unresolved_conflict_files", [])
            if isinstance(preflight.get("unresolved_conflict_files"), list)
            else []
        )
        next_commands = (
            preflight.get("next_commands", [])
            if isinstance(preflight.get("next_commands"), list)
            else []
        )

        lines = [
            f"Preflight: {'PASS' if preflight.get('ok') else 'FAIL'}",
            "",
            "Blocking reasons:",
        ]
        lines.extend([f"- {item}" for item in blocking] or ["- none"])
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
        lines.append("Checks:")
        lines.extend(
            [
                f"- {'OK' if check.get('ok') else 'FAIL'} {check.get('name')}: {check.get('detail')}"
                for check in checks
            ]
        )

        self._guidance().update("\n".join(lines))

    def _recommended_mode(self, run) -> tuple[str, str]:
        recovery = (
            run.metadata.get("recovery", {})
            if isinstance(run.metadata.get("recovery"), dict)
            else {}
        )
        details = (
            recovery.get("details", {})
            if isinstance(recovery.get("details"), dict)
            else {}
        )
        conflict_files = (
            details.get("conflict_files", [])
            if isinstance(details.get("conflict_files"), list)
            else []
        )
        if conflict_files:
            return "manual", "conflict files detected"
        return "agent_best_effort", "no explicit conflict files detected"

    def _resume_ready(self, selected_mode: str, prompt: str | None) -> tuple[bool, str]:
        if selected_mode not in {"manual", "agent_best_effort", "abort_phase"}:
            return False, "Select a recovery mode first."
        if selected_mode == "agent_best_effort" and not prompt:
            return False, "Best Effort Agent mode requires a prompt."
        return True, "Ready"

    def _refresh(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self._status().update("No active run selected.")
            return
        run = self.shell.orchestrator.get_run(run_id)
        if not run:
            self._status().update(f"Run {run_id} not found")
            return

        recovery = (
            run.metadata.get("recovery", {})
            if isinstance(run.metadata.get("recovery"), dict)
            else {}
        )
        details = (
            recovery.get("details", {})
            if isinstance(recovery.get("details"), dict)
            else {}
        )
        conflict_files = (
            details.get("conflict_files", [])
            if isinstance(details.get("conflict_files"), list)
            else []
        )
        status = present_run_status(run.status)
        mode_label = present_recovery_mode(recovery.get("selected_mode"))
        recommended_mode, recommended_reason = self._recommended_mode(run)
        ready, reason = self._resume_ready(
            str(recovery.get("selected_mode") or ""), self._prompt()
        )
        self._status().update(
            f"Run {run.id} | status={status.label} | recovery={recovery.get('status', 'none')} | "
            f"mode={mode_label} | conflicts={len(conflict_files)}\n"
            f"Recommended mode: {present_recovery_mode(recommended_mode)} ({recommended_reason})\n"
            f"Next: {status.next_action}\nResume gate: {'ready' if ready else 'blocked'} ({reason})"
        )

        if self._last_preflight is None and run.status == "paused_recovery_required":
            self._last_preflight = self.shell.orchestrator.recovery_preflight(run_id)
            self._render_preflight(self._last_preflight)

    def _set_mode(self, mode: str) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            return
        if mode == "agent_best_effort" and not self._prompt():
            self._status().update(
                "Best Effort Agent requires a prompt before mode selection."
            )
            return
        ok = self.shell.orchestrator.set_recovery_mode(
            run_id, mode, prompt=self._prompt()
        )
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
            run = self.shell.orchestrator.get_run(run_id)
            if run:
                recovery = (
                    run.metadata.get("recovery", {})
                    if isinstance(run.metadata.get("recovery"), dict)
                    else {}
                )
                ready, reason = self._resume_ready(
                    str(recovery.get("selected_mode") or ""), self._prompt()
                )
                if not ready:
                    self._status().update(f"Resume blocked: {reason}")
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
                self._status().update(
                    "Resume failed. Check preflight output and selected mode."
                )
        elif button_id == "show-worktree":
            run_id = self.shell.current_run_id
            if not run_id:
                self._status().update("No active run selected.")
                return
            run = self.shell.orchestrator.get_run(run_id)
            if not run:
                self._status().update(f"Run {run_id} not found")
                return
            recovery = (
                run.metadata.get("recovery", {})
                if isinstance(run.metadata.get("recovery"), dict)
                else {}
            )
            details = (
                recovery.get("details", {})
                if isinstance(recovery.get("details"), dict)
                else {}
            )
            worktree = details.get("worktree")
            if not worktree:
                self._status().update("No recovery worktree path available.")
                return
            result = open_local_path(Path(str(worktree)))
            if result.ok:
                self._status().update(f"Opened worktree: {worktree}")
            else:
                self._status().update(f"Unable to open worktree. {result.message}")
        elif button_id == "show-commands":
            preflight = self._last_preflight or {}
            commands = (
                preflight.get("next_commands")
                if isinstance(preflight.get("next_commands"), list)
                else []
            )
            if commands:
                command_text = "\n".join(str(item) for item in commands)
                copied = copy_text_to_clipboard(command_text)
                if copied.ok:
                    self._status().update("Recovery commands copied to clipboard.")
                else:
                    self._status().update(
                        f"Clipboard copy failed ({copied.message}). Commands remain visible in guidance panel."
                    )
            else:
                self._status().update("No recovery commands available.")
