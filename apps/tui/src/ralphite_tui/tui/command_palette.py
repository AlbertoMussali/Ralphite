from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

from ralphite_engine import PaletteCommand


class CommandPaletteScreen(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "dismiss_none", "Close"),
        ("enter", "select", "Select"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    CSS = """
    CommandPaletteScreen {
      align: center middle;
    }

    #palette {
      width: 80;
      height: 24;
      border: round $accent;
      background: $panel;
      padding: 1;
    }

    #palette-title {
      margin-bottom: 1;
    }

    #palette-table {
      height: 1fr;
      margin-top: 1;
    }
    """

    def __init__(self, commands: list[PaletteCommand]) -> None:
        super().__init__()
        self.commands = commands
        self._filtered: list[PaletteCommand] = list(commands)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette"):
            yield Static("Command Palette", id="palette-title")
            yield Input(placeholder="Type command...", id="palette-filter")
            yield DataTable(id="palette-table")

    def on_mount(self) -> None:
        table = self.query_one("#palette-table", DataTable)
        table.add_columns("Command", "Scope", "Shortcut")
        self._refresh_table()
        self.query_one("#palette-filter", Input).focus()

    def _refresh_table(self) -> None:
        table = self.query_one("#palette-table", DataTable)
        table.clear()
        for command in self._filtered:
            table.add_row(command.title, command.scope, command.shortcut or "")
        if self._filtered:
            table.move_cursor(row=0, column=0)

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value.strip().lower()
        if not value:
            self._filtered = list(self.commands)
        else:
            self._filtered = [
                command
                for command in self.commands
                if value in command.title.lower() or value in command.id.lower() or value in command.scope.lower()
            ]
        self._refresh_table()

    def action_cursor_down(self) -> None:
        table = self.query_one("#palette-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one("#palette-table", DataTable)
        table.action_cursor_up()

    def action_select(self) -> None:
        if not self._filtered:
            self.dismiss(None)
            return
        table = self.query_one("#palette-table", DataTable)
        row_index = table.cursor_row
        if row_index is None:
            self.dismiss(None)
            return
        if row_index < 0 or row_index >= len(self._filtered):
            self.dismiss(None)
            return
        self.dismiss(self._filtered[row_index].id)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
