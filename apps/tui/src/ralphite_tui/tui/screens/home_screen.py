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
        yield Static("Primary loop: setup run -> phase timeline -> recovery (if needed) -> summary.")
        yield Static("Use top navigation or `ctrl+p`/`:` command palette for every action.")
        yield Static("Task source remains file-based; TUI controls execution structure and run flow.")
