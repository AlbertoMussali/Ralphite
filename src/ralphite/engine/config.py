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
    task_writeback_mode: Literal["in_place", "revision_only", "disabled"] = (
        "revision_only"
    )
    default_backend: Literal["codex", "cursor"] = "codex"
    default_model: str = "gpt-5.3-codex"
    default_reasoning_effort: Literal["low", "medium", "high"] = "medium"
    cursor_command: str = "agent"


_TOOL_ENTRY_RE = re.compile(r"^tool:(\*|[A-Za-z0-9._/-]+)$")
_MCP_ENTRY_RE = re.compile(r"^mcp:(\*|[A-Za-z0-9._/-]+)$")


def _as_string_list(value: object, *, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        entries = [value]
    elif isinstance(value, list):
        entries = [item for item in value if isinstance(item, str)]
    else:
        return list(default)
    cleaned = [item.strip() for item in entries if item and item.strip()]
    return cleaned if cleaned else list(default)


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _sanitize_entries(
    values: list[str], pattern: re.Pattern[str], *, fallback: list[str]
) -> list[str]:
    cleaned = [item for item in _dedupe_preserve(values) if pattern.match(item)]
    if cleaned:
        return cleaned
    return list(fallback)


def ensure_workspace_layout(workspace_root: Path) -> dict[str, Path]:
    root = workspace_root.expanduser().resolve()
    dot = root / ".ralphite"
    paths = {
        "root": root,
        "dot": dot,
        "plans": dot / "plans",
        "runs": dot / "runs",
        "artifacts": dot / "artifacts",
        "config": dot / "config.toml",
        "history": dot / "runs" / "history.json",
    }
    for key in ("dot", "plans", "runs", "artifacts"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def load_config(workspace_root: Path, *, create_if_missing: bool = True) -> LocalConfig:
    paths = ensure_workspace_layout(workspace_root)
    cfg_path = paths["config"]
    if not cfg_path.exists():
        cfg = LocalConfig(workspace_root=str(paths["root"]))
        if create_if_missing:
            save_config(paths["root"], cfg)
        return cfg

    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    profile = raw.get("profile", {}) if isinstance(raw.get("profile"), dict) else {}
    policy = raw.get("policy", {}) if isinstance(raw.get("policy"), dict) else {}
    ui = raw.get("ui", {}) if isinstance(raw.get("ui"), dict) else {}
    run = raw.get("run", {}) if isinstance(raw.get("run"), dict) else {}
    writeback_mode = str(run.get("task_writeback_mode") or "revision_only")
    if writeback_mode not in {"in_place", "revision_only", "disabled"}:
        writeback_mode = "revision_only"
    default_backend = str(run.get("default_backend") or "codex")
    if default_backend not in {"codex", "cursor"}:
        default_backend = "codex"
    default_reasoning_effort = str(run.get("default_reasoning_effort") or "medium")
    if default_reasoning_effort not in {"low", "medium", "high"}:
        default_reasoning_effort = "medium"

    profile_name = str(profile.get("name", "default") or "default")
    default_plan_raw = run.get("default_plan")
    default_plan = (
        str(default_plan_raw).strip() if isinstance(default_plan_raw, str) else None
    )
    default_model_raw = run.get("default_model")
    default_model = (
        str(default_model_raw).strip()
        if isinstance(default_model_raw, str) and default_model_raw.strip()
        else "gpt-5.3-codex"
    )
    cursor_command_raw = run.get("cursor_command")
    cursor_command = (
        str(cursor_command_raw).strip()
        if isinstance(cursor_command_raw, str) and cursor_command_raw.strip()
        else "agent"
    )
    candidate = LocalConfig(
        workspace_root=str(paths["root"]),
        profile_name=profile_name,
        allow_tools=_as_string_list(policy.get("allow_tools"), default=["tool:*"]),
        deny_tools=_as_string_list(policy.get("deny_tools"), default=[]),
        allow_mcps=_as_string_list(policy.get("allow_mcps"), default=["mcp:*"]),
        deny_mcps=_as_string_list(policy.get("deny_mcps"), default=[]),
        compact_timeline=bool(ui.get("compact_timeline", False)),
        default_plan=default_plan or None,
        task_writeback_mode=writeback_mode,
        default_backend=default_backend,  # type: ignore[arg-type]
        default_model=default_model,
        default_reasoning_effort=default_reasoning_effort,  # type: ignore[arg-type]
        cursor_command=cursor_command,
    )
    issues = validate_local_config(candidate, workspace_root=paths["root"])
    if not issues:
        return candidate

    sanitized_default_plan = candidate.default_plan
    if (
        sanitized_default_plan
        and resolve_default_plan_path(paths["root"], sanitized_default_plan) is None
    ):
        sanitized_default_plan = None
    return LocalConfig(
        workspace_root=str(paths["root"]),
        profile_name=candidate.profile_name or "default",
        allow_tools=_sanitize_entries(
            candidate.allow_tools, _TOOL_ENTRY_RE, fallback=["tool:*"]
        ),
        deny_tools=_sanitize_entries(candidate.deny_tools, _TOOL_ENTRY_RE, fallback=[]),
        allow_mcps=_sanitize_entries(
            candidate.allow_mcps, _MCP_ENTRY_RE, fallback=["mcp:*"]
        ),
        deny_mcps=_sanitize_entries(candidate.deny_mcps, _MCP_ENTRY_RE, fallback=[]),
        compact_timeline=bool(candidate.compact_timeline),
        default_plan=sanitized_default_plan,
        task_writeback_mode=(
            candidate.task_writeback_mode
            if candidate.task_writeback_mode
            in {"in_place", "revision_only", "disabled"}
            else "revision_only"
        ),
        default_backend=(
            candidate.default_backend
            if candidate.default_backend in {"codex", "cursor"}
            else "codex"
        ),  # type: ignore[arg-type]
        default_model=candidate.default_model.strip() or "gpt-5.3-codex",
        default_reasoning_effort=(
            candidate.default_reasoning_effort
            if candidate.default_reasoning_effort in {"low", "medium", "high"}
            else "medium"
        ),  # type: ignore[arg-type]
        cursor_command=candidate.cursor_command.strip() or "agent",
    )


def resolve_default_plan_path(
    workspace_root: Path, default_plan: str | None
) -> Path | None:
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


def validate_local_config(
    config: LocalConfig, workspace_root: Path | None = None
) -> list[str]:
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
    if config.default_backend not in {"codex", "cursor"}:
        issues.append("invalid default_backend")
    if config.default_reasoning_effort not in {"low", "medium", "high"}:
        issues.append("invalid default_reasoning_effort")
    if not config.default_model.strip():
        issues.append("default_model cannot be empty")
    if not config.cursor_command.strip():
        issues.append("cursor_command cannot be empty")

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
            f"name = {json.dumps(config.profile_name)}",
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
            f"default_backend = {json.dumps(config.default_backend)}",
            f"default_model = {json.dumps(config.default_model)}",
            f"default_reasoning_effort = {json.dumps(config.default_reasoning_effort)}",
            f"cursor_command = {json.dumps(config.cursor_command)}",
            "",
        ]
    )
    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path
