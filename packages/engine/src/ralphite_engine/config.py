from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LocalConfig(BaseModel):
    workspace_root: str
    profile_name: str = "default"
    allow_tools: list[str] = Field(default_factory=lambda: ["tool:*"])
    deny_tools: list[str] = Field(default_factory=list)
    allow_mcps: list[str] = Field(default_factory=lambda: ["mcp:*"])
    deny_mcps: list[str] = Field(default_factory=list)
    compact_timeline: bool = False
    default_plan: str | None = None
    task_writeback_mode: Literal["in_place", "revision_only", "disabled"] = "revision_only"


_TOOL_ENTRY_RE = re.compile(r"^tool:(\*|[A-Za-z0-9._/-]+)$")
_MCP_ENTRY_RE = re.compile(r"^mcp:(\*|[A-Za-z0-9._/-]+)$")


def ensure_workspace_layout(workspace_root: Path) -> dict[str, Path]:
    root = workspace_root.expanduser().resolve()
    dot = root / ".ralphite"
    paths = {
        "root": root,
        "dot": dot,
        "plans": dot / "plans",
        "runs": dot / "runs",
        "artifacts": dot / "artifacts",
        "drafts": dot / "drafts",
        "config": dot / "config.toml",
        "history": dot / "runs" / "history.json",
    }
    for key in ("dot", "plans", "runs", "artifacts", "drafts"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def load_config(workspace_root: Path) -> LocalConfig:
    paths = ensure_workspace_layout(workspace_root)
    cfg_path = paths["config"]
    if not cfg_path.exists():
        cfg = LocalConfig(workspace_root=str(paths["root"]))
        save_config(paths["root"], cfg)
        return cfg

    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    profile = raw.get("profile", {})
    policy = raw.get("policy", {})
    ui = raw.get("ui", {})
    run = raw.get("run", {})
    writeback_mode = str(run.get("task_writeback_mode") or "revision_only")
    if writeback_mode not in {"in_place", "revision_only", "disabled"}:
        writeback_mode = "revision_only"

    return LocalConfig(
        workspace_root=str(paths["root"]),
        profile_name=profile.get("name", "default"),
        allow_tools=list(policy.get("allow_tools", ["tool:*"])),
        deny_tools=list(policy.get("deny_tools", [])),
        allow_mcps=list(policy.get("allow_mcps", ["mcp:*"])),
        deny_mcps=list(policy.get("deny_mcps", [])),
        compact_timeline=bool(ui.get("compact_timeline", False)),
        default_plan=run.get("default_plan") or None,
        task_writeback_mode=writeback_mode,
    )


def resolve_default_plan_path(workspace_root: Path, default_plan: str | None) -> Path | None:
    raw = (default_plan or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    search = [candidate]
    root = workspace_root.expanduser().resolve()
    if not candidate.is_absolute():
        search.extend([root / candidate, root / ".ralphite" / "plans" / candidate])
    for item in search:
        if item.exists() and item.is_file():
            return item.resolve()
    return None


def validate_local_config(config: LocalConfig, workspace_root: Path | None = None) -> list[str]:
    issues: list[str] = []
    seen: set[str] = set()

    for entry in config.allow_tools:
        if not _TOOL_ENTRY_RE.match(entry or ""):
            issues.append(f"invalid tool allow entry: {entry}")
        key = f"allow_tools:{entry}"
        if key in seen:
            issues.append(f"duplicate tool allow entry: {entry}")
        seen.add(key)

    for entry in config.deny_tools:
        if not _TOOL_ENTRY_RE.match(entry or ""):
            issues.append(f"invalid tool deny entry: {entry}")
        key = f"deny_tools:{entry}"
        if key in seen:
            issues.append(f"duplicate tool deny entry: {entry}")
        seen.add(key)

    for entry in config.allow_mcps:
        if not _MCP_ENTRY_RE.match(entry or ""):
            issues.append(f"invalid mcp allow entry: {entry}")
        key = f"allow_mcps:{entry}"
        if key in seen:
            issues.append(f"duplicate mcp allow entry: {entry}")
        seen.add(key)

    for entry in config.deny_mcps:
        if not _MCP_ENTRY_RE.match(entry or ""):
            issues.append(f"invalid mcp deny entry: {entry}")
        key = f"deny_mcps:{entry}"
        if key in seen:
            issues.append(f"duplicate mcp deny entry: {entry}")
        seen.add(key)

    if config.task_writeback_mode not in {"in_place", "revision_only", "disabled"}:
        issues.append("invalid task_writeback_mode")

    if workspace_root is not None and (config.default_plan or "").strip():
        if resolve_default_plan_path(workspace_root, config.default_plan) is None:
            issues.append(f"default_plan not found: {config.default_plan}")

    return issues


def _toml_list(items: list[str]) -> str:
    return json.dumps(items)


def save_config(workspace_root: Path, config: LocalConfig) -> Path:
    paths = ensure_workspace_layout(workspace_root)
    issues = validate_local_config(config, workspace_root=paths["root"])
    if issues:
        raise ValueError("; ".join(issues))
    cfg_path = paths["config"]
    text = "\n".join(
        [
            "# Ralphite local profile",
            "[profile]",
            f'name = {json.dumps(config.profile_name)}',
            "",
            "[policy]",
            f"allow_tools = {_toml_list(config.allow_tools)}",
            f"deny_tools = {_toml_list(config.deny_tools)}",
            f"allow_mcps = {_toml_list(config.allow_mcps)}",
            f"deny_mcps = {_toml_list(config.deny_mcps)}",
            "",
            "[ui]",
            f"compact_timeline = {'true' if config.compact_timeline else 'false'}",
            "",
            "[run]",
            f"default_plan = {json.dumps(config.default_plan or '')}",
            f"task_writeback_mode = {json.dumps(config.task_writeback_mode)}",
            "",
        ]
    )
    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path
