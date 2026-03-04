from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Static

from ralphite_engine import LocalOrchestrator, PaletteCommand, migrate_plan_in_place
from ralphite_tui.tui.command_palette import CommandPaletteScreen
from ralphite_tui.tui.editor_screen import EditorScreen
from ralphite_tui.tui.screens.artifacts_screen import ArtifactsScreen
from ralphite_tui.tui.screens.history_screen import HistoryScreen
from ralphite_tui.tui.screens.home_screen import HomeScreen
from ralphite_tui.tui.screens.migration_screen import MigrationScreen
from ralphite_tui.tui.screens.run_detail_screen import RunDetailScreen
from ralphite_tui.tui.screens.runs_screen import RunsScreen
from ralphite_tui.tui.screens.settings_screen import SettingsScreen


class AppShell(App[None]):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+p", "open_palette", "Palette"),
        (":", "open_palette", "Palette"),
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
        self._registry: dict[str, Callable[[], None]] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="top-nav"):
            yield Static("Ralphite", id="nav-title")
            for screen in ["home", "editor", "runs", "run_detail", "artifacts", "history", "settings", "migration"]:
                yield Button(screen.replace("_", " ").title(), id=f"nav-{screen}")
        yield Vertical(id="screen-body")
        yield Footer()

    def on_mount(self) -> None:
        self.show_screen(self.initial_screen, push=False)

    def show_screen(self, screen: str, *, push: bool = True) -> None:
        factories: dict[str, Callable[[], Any]] = {
            "home": HomeScreen,
            "editor": EditorScreen,
            "runs": RunsScreen,
            "run_detail": RunDetailScreen,
            "artifacts": ArtifactsScreen,
            "history": HistoryScreen,
            "settings": SettingsScreen,
            "migration": MigrationScreen,
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

    def _dispatch_screen_local(self, action_name: str) -> bool:
        if self._current_widget is None:
            return False
        method = getattr(self._current_widget, action_name, None)
        if callable(method):
            method()
            return True
        return False

    def _command_map(self) -> tuple[list[PaletteCommand], dict[str, Callable[[], None]]]:
        commands: list[PaletteCommand] = [
            PaletteCommand(id="nav.home", title="Go Home", scope="global", shortcut="1"),
            PaletteCommand(id="nav.editor", title="Open Editor", scope="global", shortcut="2"),
            PaletteCommand(id="nav.runs", title="Open Runs", scope="global", shortcut="3"),
            PaletteCommand(id="nav.run_detail", title="Open Run Detail", scope="global", shortcut="4"),
            PaletteCommand(id="nav.artifacts", title="Open Artifacts", scope="global", shortcut="5"),
            PaletteCommand(id="nav.history", title="Open History", scope="global", shortcut="6"),
            PaletteCommand(id="nav.settings", title="Open Settings", scope="global", shortcut="7"),
            PaletteCommand(id="nav.migration", title="Open Migration", scope="global", shortcut="8"),
            PaletteCommand(id="run.start", title="Start Run", scope="run", shortcut="s"),
            PaletteCommand(id="run.pause", title="Pause Run", scope="run", shortcut="p"),
            PaletteCommand(id="run.resume", title="Resume Run", scope="run", shortcut="r"),
            PaletteCommand(id="run.cancel", title="Cancel Run", scope="run", shortcut="c"),
            PaletteCommand(id="run.recover", title="Recover Latest", scope="run"),
            PaletteCommand(id="migration.strict", title="Run Strict Migration", scope="workspace"),
            PaletteCommand(id="editor.validate", title="Validate Plan", scope="editor", shortcut="ctrl+v"),
            PaletteCommand(id="editor.save", title="Save Plan", scope="editor", shortcut="ctrl+s"),
            PaletteCommand(id="editor.fix", title="Apply Autofix", scope="editor", shortcut="ctrl+f"),
        ]

        handlers: dict[str, Callable[[], None]] = {
            "nav.home": lambda: self.show_screen("home"),
            "nav.editor": lambda: self.show_screen("editor"),
            "nav.runs": lambda: self.show_screen("runs"),
            "nav.run_detail": lambda: self.show_screen("run_detail"),
            "nav.artifacts": lambda: self.show_screen("artifacts"),
            "nav.history": lambda: self.show_screen("history"),
            "nav.settings": lambda: self.show_screen("settings"),
            "nav.migration": lambda: self.show_screen("migration"),
            "run.start": self.action_start_run,
            "run.pause": self.action_pause_run,
            "run.resume": self.action_resume_run,
            "run.cancel": self.action_cancel_run,
            "run.recover": self.action_recover_run,
            "migration.strict": lambda: self.run_strict_migration(),
            "editor.validate": lambda: self._dispatch_screen_local("action_validate"),
            "editor.save": lambda: self._dispatch_screen_local("action_save"),
            "editor.fix": lambda: self._dispatch_screen_local("action_apply_fix"),
        }
        return commands, handlers

    def action_open_palette(self) -> None:
        commands, handlers = self._command_map()
        self._registry = handlers

        def _on_selected(command_id: str | None) -> None:
            if not command_id:
                return
            handler = self._registry.get(command_id)
            if handler:
                handler()

        self.push_screen(CommandPaletteScreen(commands), _on_selected)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("nav-"):
            self.show_screen(button_id.split("nav-", 1)[1])

    def action_start_run(self) -> None:
        run_id = self.orchestrator.start_run(metadata={"source": "tui.shell"})
        self.current_run_id = run_id

    def action_pause_run(self) -> None:
        if self.current_run_id:
            self.orchestrator.pause_run(self.current_run_id)

    def action_resume_run(self) -> None:
        if self.current_run_id:
            self.orchestrator.resume_run(self.current_run_id)

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

    def run_strict_migration(self) -> tuple[bool, list[str]]:
        messages: list[str] = []
        failed = False
        for plan in self.orchestrator.list_plans():
            result = migrate_plan_in_place(Path(plan))
            if result.changed:
                messages.append(f"migrated {result.source.name}")
            else:
                messages.append(f"checked {result.source.name}")
            for warning in result.warnings:
                messages.append(f"  - {warning}")
            if not result.valid:
                failed = True
                for issue in result.issues:
                    messages.append(
                        f"  - BLOCK {issue.get('code')}: {issue.get('message')} ({issue.get('path')})"
                    )
        return (not failed), messages
