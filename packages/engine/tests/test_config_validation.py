from __future__ import annotations

from pathlib import Path

import pytest

from ralphite_engine.config import LocalConfig, resolve_default_plan_path, save_config, validate_local_config


def _config(tmp_path: Path) -> LocalConfig:
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


def test_validate_local_config_rejects_malformed_policy_entries(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.allow_tools = ["tool:*", "badtool"]
    cfg.allow_mcps = ["mcp:*", "mcp:"]
    issues = validate_local_config(cfg, workspace_root=tmp_path)
    assert any("invalid tool allow entry" in issue for issue in issues)
    assert any("invalid mcp allow entry" in issue for issue in issues)


def test_validate_local_config_rejects_missing_default_plan(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.default_plan = "missing.yaml"
    issues = validate_local_config(cfg, workspace_root=tmp_path)
    assert any("default_plan not found" in issue for issue in issues)


def test_save_config_raises_on_invalid_entries(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.deny_tools = ["tool:*", "tool:*"]
    with pytest.raises(ValueError):
        save_config(tmp_path, cfg)


def test_resolve_default_plan_path_finds_plan_in_workspace(tmp_path: Path) -> None:
    plan = tmp_path / ".ralphite" / "plans" / "demo.yaml"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("version: 4\nplan_id: demo\nname: demo\n", encoding="utf-8")
    resolved = resolve_default_plan_path(tmp_path, "demo.yaml")
    assert resolved == plan.resolve()
