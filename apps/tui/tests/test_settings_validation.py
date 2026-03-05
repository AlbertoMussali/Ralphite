from __future__ import annotations

from pathlib import Path

from ralphite_engine.config import LocalConfig, load_config, save_config
from ralphite_tui.tui.screens.settings_screen import save_settings_with_rollback


def _valid_config(tmp_path: Path) -> LocalConfig:
    return LocalConfig(
        workspace_root=str(tmp_path),
        profile_name="default",
        allow_tools=["tool:*"],
        deny_tools=[],
        allow_mcps=["mcp:*"],
        deny_mcps=[],
        compact_timeline=False,
        default_plan=None,
        task_writeback_mode="revision_only",
    )


def test_save_settings_with_rollback_rejects_invalid_mode(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    cfg.task_writeback_mode = "revision_only"
    save_config(tmp_path, cfg)

    invalid = _valid_config(tmp_path)
    invalid.task_writeback_mode = "revision_only"
    invalid.allow_tools = ["invalid"]
    ok, issues, detail = save_settings_with_rollback(tmp_path, invalid)
    assert ok is False
    assert detail == "validation_failed"
    assert any("invalid tool allow entry" in issue for issue in issues)

    loaded = load_config(tmp_path)
    assert loaded.allow_tools == ["tool:*"]


def test_save_settings_with_rollback_rejects_missing_default_plan(
    tmp_path: Path,
) -> None:
    cfg = _valid_config(tmp_path)
    save_config(tmp_path, cfg)

    invalid = _valid_config(tmp_path)
    invalid.default_plan = "missing-plan.yaml"
    ok, issues, detail = save_settings_with_rollback(tmp_path, invalid)
    assert ok is False
    assert detail == "validation_failed"
    assert any("default_plan not found" in issue for issue in issues)


def test_save_settings_with_rollback_persists_valid_update(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    save_config(tmp_path, cfg)
    plan = tmp_path / ".ralphite" / "plans" / "demo.yaml"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("version: 5\nplan_id: demo\nname: demo\n", encoding="utf-8")

    updated = _valid_config(tmp_path)
    updated.default_plan = str(plan)
    updated.task_writeback_mode = "in_place"
    ok, issues, detail = save_settings_with_rollback(tmp_path, updated)
    assert ok is True
    assert issues == []
    assert "config.toml" in detail

    loaded = load_config(tmp_path)
    assert loaded.task_writeback_mode == "in_place"
