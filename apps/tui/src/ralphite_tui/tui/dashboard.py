from __future__ import annotations

from ralphite_engine import LocalOrchestrator
from ralphite_tui.tui.app_shell import AppShell


class DashboardApp(AppShell):
    """Backwards-compatible alias while TUI transitions to AppShell naming."""

    def __init__(self, orchestrator: LocalOrchestrator, run_id: str | None = None) -> None:
        super().__init__(orchestrator=orchestrator, run_id=run_id)
