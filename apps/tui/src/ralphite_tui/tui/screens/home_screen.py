from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


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
        yield Static("Primary loop: start run -> inspect timeline -> inspect artifacts -> replay.")
        yield Static("Use top navigation or `ctrl+p`/`:` command palette for every action.")
        yield Static("Open Editor to create/edit a schema-safe step list plan.")
