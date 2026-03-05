from __future__ import annotations

from pathlib import Path

from ralphite_tui.tui import system_actions
from ralphite_tui.tui.system_actions import ActionResult


def test_copy_text_to_clipboard_uses_pbcopy_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(system_actions.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(
        system_actions.shutil,
        "which",
        lambda cmd: "/usr/bin/pbcopy" if cmd == "pbcopy" else None,
    )
    observed: dict[str, object] = {}

    def _fake_run(
        args: list[str], *, text: str | None = None, shell: bool = False
    ) -> ActionResult:
        observed["args"] = args
        observed["text"] = text
        observed["shell"] = shell
        return ActionResult(ok=True, message="ok", command="pbcopy")

    monkeypatch.setattr(system_actions, "_run", _fake_run)
    result = system_actions.copy_text_to_clipboard("line1\nline2")
    assert result.ok is True
    assert result.message == "Copied to clipboard."
    assert observed["args"] == ["pbcopy"]
    assert observed["text"] == "line1\nline2"
    assert observed["shell"] is False


def test_copy_text_to_clipboard_reports_missing_linux_utilities(monkeypatch) -> None:
    monkeypatch.setattr(system_actions.sys, "platform", "linux", raising=False)
    monkeypatch.setattr(system_actions.shutil, "which", lambda _cmd: None)
    result = system_actions.copy_text_to_clipboard("hello")
    assert result.ok is False
    assert "No clipboard utility found" in result.message


def test_open_local_path_returns_error_for_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    result = system_actions.open_local_path(missing)
    assert result.ok is False
    assert "Path does not exist" in result.message


def test_open_local_path_uses_open_on_macos(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(system_actions.sys, "platform", "darwin", raising=False)
    opened: dict[str, object] = {}
    target = tmp_path / "artifact.txt"
    target.write_text("x", encoding="utf-8")

    def _fake_run(
        args: list[str], *, text: str | None = None, shell: bool = False
    ) -> ActionResult:
        opened["args"] = args
        opened["text"] = text
        opened["shell"] = shell
        return ActionResult(ok=True, message="ok", command="open")

    monkeypatch.setattr(system_actions, "_run", _fake_run)
    result = system_actions.open_local_path(target)
    assert result.ok is True
    assert result.message == "Opened path."
    assert opened["args"] == ["open", str(target.resolve())]
    assert opened["text"] is None
    assert opened["shell"] is False
