from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class MigrationScreen(Vertical):
    DEFAULT_CSS = """
    MigrationScreen {
      height: 1fr;
      padding: 1;
    }
    #migration-status {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    """

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Run strict migration to enforce schema-safe plans.", id="migration-status")
        yield Button("Run Strict Migration", id="run-migration", variant="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "run-migration":
            return
        ok, messages = self.shell.run_strict_migration()
        block = "\n".join(messages) if messages else "No migration output."
        if ok:
            self.query_one("#migration-status", Static).update(f"Migration succeeded.\n{block}")
        else:
            self.query_one("#migration-status", Static).update(f"Migration blocked run.\n{block}")
