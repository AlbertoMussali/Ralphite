from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Static

from ralphite_engine import LocalOrchestrator, PaletteCommand
from ralphite_tui.tui.command_palette import CommandPaletteScreen
from ralphite_tui.tui.screens.history_screen import HistoryScreen
from ralphite_tui.tui.screens.home_screen import HomeScreen
from ralphite_tui.tui.screens.phase_timeline_screen import PhaseTimelineScreen
from ralphite_tui.tui.screens.recovery_screen import RecoveryScreen
from ralphite_tui.tui.screens.run_setup_screen import RunSetupScreen
from ralphite_tui.tui.screens.runs_screen import RunsScreen
from ralphite_tui.tui.screens.settings_screen import SettingsScreen
from ralphite_tui.tui.screens.summary_screen import SummaryScreen


class AppShell(App[None]):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+p", "command_palette", "Palette"),
        (":", "command_palette", "Palette"),
        ("1", "nav_home", "Home"),
        ("2", "nav_run_setup", "Run Setup"),
        ("3", "nav_runs", "Runs"),
        ("4", "nav_phase_timeline", "Phase Timeline"),
        ("5", "nav_recovery", "Recovery"),
        ("6", "nav_summary", "Summary"),
        ("7", "nav_history", "History"),
        ("8", "nav_settings", "Settings"),
        ("s", "start_run", "Start Run"),
        ("p", "pause_run", "Pause"),
        ("r", "resume_run", "Resume"),
        ("c", "cancel_run", "Cancel"),
    ]

    CSS = """
    Screen {
      layout: vertical;
    }
    #top-nav {
      height: auto;
      padding: 0 1;
      margin-bottom: 1;
    }
    #screen-body {
      height: 1fr;
      margin: 0 1;
    }
    #nav-title {
      margin: 0 1;
      color: $accent;
    }
    """

    def __init__(
        self,
        orchestrator: LocalOrchestrator,
        run_id: str | None = None,
        initial_screen: str = "home",
    ) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.current_run_id = run_id
        self.initial_screen = initial_screen
        self.nav_stack: list[str] = []
        self._current_widget: Any = None
        self._palette_handlers: dict[str, Callable[[], None]] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="top-nav"):
            yield Static("Ralphite", id="nav-title")
            for screen in ["home", "run_setup", "runs", "phase_timeline", "recovery", "summary", "history", "settings"]:
                yield Button(screen.replace("_", " ").title(), id=f"nav-{screen}")
        yield Vertical(id="screen-body")
        yield Footer()

    def on_mount(self) -> None:
        self.show_screen(self.initial_screen, push=False)

    def show_screen(self, screen: str, *, push: bool = True) -> None:
        factories: dict[str, Callable[[], Any]] = {
            "home": HomeScreen,
            "run_setup": RunSetupScreen,
            "runs": RunsScreen,
            "phase_timeline": PhaseTimelineScreen,
            "recovery": RecoveryScreen,
            "summary": SummaryScreen,
            "history": HistoryScreen,
            "settings": SettingsScreen,
        }
        factory = factories.get(screen)
        if not factory:
            return

        body = self.query_one("#screen-body", Vertical)
        for child in list(body.children):
            child.remove()
        self._current_widget = factory()
        body.mount(self._current_widget)
        if push:
            self.nav_stack.append(screen)
        else:
            self.nav_stack = [screen]

    def _command_map(self) -> tuple[list[PaletteCommand], dict[str, Callable[[], None]]]:
        commands: list[PaletteCommand] = [
            PaletteCommand(id="nav.home", title="Go Home", scope="global", shortcut="1"),
            PaletteCommand(id="nav.run_setup", title="Open Run Setup", scope="global", shortcut="2"),
            PaletteCommand(id="nav.runs", title="Open Runs", scope="global", shortcut="3"),
            PaletteCommand(id="nav.phase_timeline", title="Open Phase Timeline", scope="global", shortcut="4"),
            PaletteCommand(id="nav.recovery", title="Open Recovery Console", scope="global", shortcut="5"),
            PaletteCommand(id="nav.summary", title="Open Post-Run Summary", scope="global", shortcut="6"),
            PaletteCommand(id="nav.history", title="Open History", scope="global", shortcut="7"),
            PaletteCommand(id="nav.settings", title="Open Settings", scope="global", shortcut="8"),
            PaletteCommand(id="run.start", title="Start Run", scope="run", shortcut="s"),
            PaletteCommand(id="run.pause", title="Pause Run", scope="run", shortcut="p"),
            PaletteCommand(id="run.resume", title="Resume Run", scope="run", shortcut="r"),
            PaletteCommand(id="run.cancel", title="Cancel Run", scope="run", shortcut="c"),
            PaletteCommand(id="run.recover", title="Recover Latest", scope="run"),
            PaletteCommand(id="recovery.manual", title="Recovery: Manual", scope="recovery"),
            PaletteCommand(id="recovery.agent", title="Recovery: Best Effort Agent", scope="recovery"),
            PaletteCommand(id="recovery.abort", title="Recovery: Abort", scope="recovery"),
        ]

        handlers: dict[str, Callable[[], None]] = {
            "nav.home": lambda: self.show_screen("home"),
            "nav.run_setup": lambda: self.show_screen("run_setup"),
            "nav.runs": lambda: self.show_screen("runs"),
            "nav.phase_timeline": lambda: self.show_screen("phase_timeline"),
            "nav.recovery": lambda: self.show_screen("recovery"),
            "nav.summary": lambda: self.show_screen("summary"),
            "nav.history": lambda: self.show_screen("history"),
            "nav.settings": lambda: self.show_screen("settings"),
            "run.start": self.action_start_run,
            "run.pause": self.action_pause_run,
            "run.resume": self.action_resume_run,
            "run.cancel": self.action_cancel_run,
            "run.recover": self.action_recover_run,
            "recovery.manual": lambda: self.action_set_recovery_mode("manual"),
            "recovery.agent": lambda: self.action_set_recovery_mode("agent_best_effort"),
            "recovery.abort": lambda: self.action_set_recovery_mode("abort_phase"),
        }
        return commands, handlers

    def action_command_palette(self) -> None:
        commands, handlers = self._command_map()
        self._palette_handlers = handlers

        def _on_selected(command_id: str | None) -> None:
            if not command_id:
                return
            handler = self._palette_handlers.get(command_id)
            if handler:
                handler()

        self.push_screen(CommandPaletteScreen(commands), _on_selected)

    def action_open_palette(self) -> None:
        self.action_command_palette()

    def action_nav_home(self) -> None:
        self.show_screen("home")

    def action_nav_run_setup(self) -> None:
        self.show_screen("run_setup")

    def action_nav_runs(self) -> None:
        self.show_screen("runs")

    def action_nav_phase_timeline(self) -> None:
        self.show_screen("phase_timeline")

    def action_nav_recovery(self) -> None:
        self.show_screen("recovery")

    def action_nav_summary(self) -> None:
        self.show_screen("summary")

    def action_nav_history(self) -> None:
        self.show_screen("history")

    def action_nav_settings(self) -> None:
        self.show_screen("settings")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("nav-"):
            self.show_screen(button_id.split("nav-", 1)[1])

    def start_run_for_plan(self, plan_ref: str | None) -> str | None:
        try:
            run_id = self.orchestrator.start_run(plan_ref=plan_ref, metadata={"source": "tui.shell"})
        except Exception:
            return None
        self.current_run_id = run_id
        return run_id

    def action_start_run(self) -> None:
        run_id = self.start_run_for_plan(None)
        if run_id:
            self.show_screen("phase_timeline")

    def action_pause_run(self) -> None:
        if self.current_run_id:
            self.orchestrator.pause_run(self.current_run_id)

    def action_resume_run(self) -> None:
        if self.current_run_id:
            if not self.orchestrator.resume_run(self.current_run_id):
                self.show_screen("recovery")

    def action_cancel_run(self) -> None:
        if self.current_run_id:
            self.orchestrator.cancel_run(self.current_run_id)

    def action_recover_run(self) -> None:
        recoverable = self.orchestrator.list_recoverable_runs()
        if not recoverable:
            return
        run_id = recoverable[-1]
        if self.orchestrator.recover_run(run_id):
            self.current_run_id = run_id
            self.show_screen("recovery")

    def action_set_recovery_mode(self, mode: str) -> None:
        if not self.current_run_id:
            return
        if self.orchestrator.set_recovery_mode(self.current_run_id, mode):
            if mode != "abort_phase":
                self.orchestrator.resume_from_checkpoint(self.current_run_id)
                self.show_screen("phase_timeline")
            else:
                self.show_screen("summary")
