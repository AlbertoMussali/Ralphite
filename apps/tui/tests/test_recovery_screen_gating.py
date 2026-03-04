from __future__ import annotations

from ralphite_tui.tui.screens.recovery_screen import RecoveryScreen


def test_resume_ready_requires_mode() -> None:
    screen = RecoveryScreen()
    ready, reason = screen._resume_ready("", None)  # noqa: SLF001
    assert ready is False
    assert "Select a recovery mode" in reason


def test_resume_ready_requires_prompt_for_agent_mode() -> None:
    screen = RecoveryScreen()
    ready, reason = screen._resume_ready("agent_best_effort", None)  # noqa: SLF001
    assert ready is False
    assert "requires a prompt" in reason


def test_resume_ready_accepts_manual_without_prompt() -> None:
    screen = RecoveryScreen()
    ready, reason = screen._resume_ready("manual", None)  # noqa: SLF001
    assert ready is True
    assert reason == "Ready"
