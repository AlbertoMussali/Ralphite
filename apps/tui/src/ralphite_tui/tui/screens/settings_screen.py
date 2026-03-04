from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

from ralphite_engine.config import LocalConfig, load_config, save_config, validate_local_config

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


def save_settings_with_rollback(workspace_root: Path, config: LocalConfig) -> tuple[bool, list[str], str]:
    root = workspace_root.expanduser().resolve()
    cfg_path = root / ".ralphite" / "config.toml"
    backup = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else None

    issues = validate_local_config(config, workspace_root=root)
    if issues:
        return False, issues, "validation_failed"

    try:
        save_config(root, config)
        loaded = load_config(root)
        verify_issues = validate_local_config(loaded, workspace_root=root)
        if verify_issues:
            raise ValueError("; ".join(verify_issues))
    except Exception as exc:  # noqa: BLE001
        if backup is None:
            if cfg_path.exists():
                cfg_path.unlink()
        else:
            cfg_path.write_text(backup, encoding="utf-8")
        return False, [str(exc)], "rollback_applied"

    return True, [], str(cfg_path)


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
        yield Static("Settings", classes="title")
        yield Static("Edit local policy and run defaults.", id="settings-status")
        yield Input(placeholder="Profile name", id="cfg-profile")
        yield Input(placeholder="Allow tools (comma-separated)", id="cfg-allow-tools")
        yield Input(placeholder="Deny tools (comma-separated)", id="cfg-deny-tools")
        yield Input(placeholder="Allow MCPs (comma-separated)", id="cfg-allow-mcps")
        yield Input(placeholder="Deny MCPs (comma-separated)", id="cfg-deny-mcps")
        yield Input(placeholder="Default plan path/name", id="cfg-default-plan")
        yield Input(placeholder="Write-back mode: revision_only | in_place | disabled", id="cfg-writeback")
        with Horizontal():
            yield Button("Preset: Open", id="preset-open")
            yield Button("Preset: Balanced", id="preset-balanced")
            yield Button("Preset: Restricted", id="preset-restricted")
            yield Button("Save", id="save-settings", variant="success")
            yield Button("Reload", id="reload-settings")

    def on_mount(self) -> None:
        self._load_from_config()

    def _status(self) -> Static:
        return self.query_one("#settings-status", Static)

    def _set_value(self, widget_id: str, value: str) -> None:
        self.query_one(f"#{widget_id}", Input).value = value

    def _get_list(self, widget_id: str) -> list[str]:
        value = self.query_one(f"#{widget_id}", Input).value
        return [item.strip() for item in value.split(",") if item.strip()]

    def _load_from_config(self) -> None:
        cfg = self.shell.orchestrator.config
        self._set_value("cfg-profile", cfg.profile_name)
        self._set_value("cfg-allow-tools", ", ".join(cfg.allow_tools))
        self._set_value("cfg-deny-tools", ", ".join(cfg.deny_tools))
        self._set_value("cfg-allow-mcps", ", ".join(cfg.allow_mcps))
        self._set_value("cfg-deny-mcps", ", ".join(cfg.deny_mcps))
        self._set_value("cfg-default-plan", cfg.default_plan or "")
        self._set_value("cfg-writeback", cfg.task_writeback_mode)
        self._status().update("Loaded settings from .ralphite/config.toml.")

    def _apply_preset(self, preset: str) -> None:
        if preset == "open":
            self._set_value("cfg-allow-tools", "tool:*")
            self._set_value("cfg-deny-tools", "")
            self._set_value("cfg-allow-mcps", "mcp:*")
            self._set_value("cfg-deny-mcps", "")
        elif preset == "balanced":
            self._set_value("cfg-allow-tools", "tool:*")
            self._set_value("cfg-deny-tools", "tool:dangerous")
            self._set_value("cfg-allow-mcps", "mcp:*")
            self._set_value("cfg-deny-mcps", "")
        elif preset == "restricted":
            self._set_value("cfg-allow-tools", "")
            self._set_value("cfg-deny-tools", "tool:*")
            self._set_value("cfg-allow-mcps", "")
            self._set_value("cfg-deny-mcps", "mcp:*")
        self._status().update(f"Applied {preset} preset. Save to persist.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "reload-settings":
            self._load_from_config()
            return
        if button_id == "preset-open":
            self._apply_preset("open")
            return
        if button_id == "preset-balanced":
            self._apply_preset("balanced")
            return
        if button_id == "preset-restricted":
            self._apply_preset("restricted")
            return
        if button_id != "save-settings":
            return

        writeback_mode = self.query_one("#cfg-writeback", Input).value.strip() or "revision_only"
        if writeback_mode not in {"revision_only", "in_place", "disabled"}:
            self._status().update("Invalid write-back mode. Use revision_only, in_place, or disabled.")
            return

        current = self.shell.orchestrator.config
        updated = LocalConfig(
            workspace_root=current.workspace_root,
            profile_name=self.query_one("#cfg-profile", Input).value.strip() or "default",
            allow_tools=self._get_list("cfg-allow-tools"),
            deny_tools=self._get_list("cfg-deny-tools"),
            allow_mcps=self._get_list("cfg-allow-mcps"),
            deny_mcps=self._get_list("cfg-deny-mcps"),
            compact_timeline=current.compact_timeline,
            default_plan=self.query_one("#cfg-default-plan", Input).value.strip() or None,
            task_writeback_mode=writeback_mode,  # type: ignore[arg-type]
        )
        ok, issues, detail = save_settings_with_rollback(self.shell.orchestrator.workspace_root, updated)
        if not ok:
            self.shell.orchestrator.config = load_config(self.shell.orchestrator.workspace_root)
            preview = issues[0] if issues else "unknown error"
            self._status().update(f"Settings not saved ({detail}): {preview}")
            return
        self.shell.orchestrator.config = load_config(self.shell.orchestrator.workspace_root)
        self._status().update(f"Settings saved: {detail}")
