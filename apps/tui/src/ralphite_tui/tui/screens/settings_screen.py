from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class SettingsScreen(Vertical):
    DEFAULT_CSS = """
    SettingsScreen {
      height: 1fr;
      padding: 1;
      border: round $surface;
    }
    """

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        cfg = self.shell.orchestrator.config
        yield Static("Settings", classes="title")
        yield Static(f"Profile: {cfg.profile_name}")
        yield Static(f"Allow tools: {cfg.allow_tools}")
        yield Static(f"Deny tools: {cfg.deny_tools}")
        yield Static(f"Allow MCPs: {cfg.allow_mcps}")
        yield Static(f"Deny MCPs: {cfg.deny_mcps}")
        yield Static(f"Compact timeline: {cfg.compact_timeline}")
