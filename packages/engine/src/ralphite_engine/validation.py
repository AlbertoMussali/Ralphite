from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
import subprocess
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from ralphite_engine.models import ValidationFix
from ralphite_engine.structure_compiler import (
    RuntimeExecutionPlan,
    compile_execution_structure,
)
from ralphite_engine.task_parser import ParsedTask, parse_plan_tasks
from ralphite_schemas.plan_v5 import PlanSpecV5
from ralphite_schemas.validation import ValidationError, compile_plan, validate_plan


UNSUPPORTED_VERSION_MESSAGE = "Invalid plan version. Ralphite executes only version: 5 unified YAML (tasks + orchestration + agents)."


PlanDocument = PlanSpecV5


def parse_plan_yaml(content: str) -> PlanDocument:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("plan content must be a YAML object")
    version = int(data.get("version", 1))
    if version != 5:
        raise ValueError(UNSUPPORTED_VERSION_MESSAGE)
    return PlanSpecV5.model_validate(data)


def _collect_profile_tools(plan: PlanSpecV5) -> tuple[list[str], list[str]]:
    tools = sorted(
        {
            entry
            for profile in plan.agents
            for entry in profile.tools_allow
            if isinstance(entry, str) and entry.startswith("tool:")
        }
    )
    mcps = sorted(
        {
            entry
            for profile in plan.agents
            for entry in profile.tools_allow
            if isinstance(entry, str) and entry.startswith("mcp:")
        }
    )
    return tools, mcps


def _git_recovery_readiness(workspace_root: str | Path | None) -> dict[str, Any]:
    if workspace_root is None:
        return {"status": "unresolved", "reason": "workspace root unavailable"}

    root = Path(workspace_root).expanduser().resolve()
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return {
            "status": "degraded",
            "reason": "workspace is not a git work tree; worktree integration will run in simulation mode",
        }

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    dirty = bool(status.stdout.strip())
    return {
        "status": "ready" if not dirty else "dirty",
        "base_branch_clean": not dirty,
        "reason": "working tree has uncommitted changes" if dirty else "ok",
    }


def _append_task_diagnostics(
    *,
    plan: PlanSpecV5,
    issues: list[dict[str, Any]],
) -> tuple[list[str], RuntimeExecutionPlan | None, list[ParsedTask]]:
    tasks, parse_issues = parse_plan_tasks(plan)
    if parse_issues:
        for issue in parse_issues:
            issues.append(
                {
                    "code": "tasks.parse_error",
                    "message": issue,
                    "path": "tasks",
                    "level": "error",
                }
            )

    compiled_runtime, compile_issues = compile_execution_structure(
        plan, tasks, task_parse_issues=parse_issues
    )
    for issue in compile_issues:
        issues.append(
            {
                "code": "run.invalid",
                "message": issue,
                "path": "orchestration",
                "level": "error",
            }
        )
    return parse_issues, compiled_runtime, tasks


def _dedupe_and_sort_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for issue in issues:
        code = str(issue.get("code") or "")
        path = str(issue.get("path") or "")
        message = str(issue.get("message") or "")
        level = str(issue.get("level") or "error")
        deduped.setdefault((code, path, message, level), issue)
    return sorted(
        deduped.values(),
        key=lambda item: (
            str(item.get("path") or ""),
            str(item.get("code") or ""),
            str(item.get("message") or ""),
            str(item.get("level") or ""),
        ),
    )


def _recommended_commands(
    issues: list[dict[str, Any]],
    *,
    plan_path: str | Path | None = None,
) -> list[str]:
    commands: list[str] = []
    target = str(plan_path) if plan_path else ".ralphite/plans/<plan>.yaml"
    codes = {
        str(issue.get("code") or "") for issue in issues if isinstance(issue, dict)
    }
    if "version.invalid" in codes:
        commands.append(
            'uv run ralphite init --workspace . --yes --template general_sps --plan-id migrated_v5 --name "Migrated V5"'
        )
    if "agent.missing_worker" in codes or "agent.missing_orchestrator" in codes:
        commands.append(
            f"uv run ralphite validate --workspace . --plan {target} --apply-safe-fixes --json"
        )
    if any(code.startswith("task.dep_") for code in codes):
        commands.append(
            f"uv run ralphite validate --workspace . --plan {target} --json"
        )
    if "tasks.unassigned" in codes or "tasks.routing.missing" in codes:
        commands.append(
            "Open Run Setup and assign routing.lane / routing.cell for pending tasks, then validate again."
        )
    if "agent.provider.legacy_openai" in codes:
        commands.append(
            "Update agents provider to codex/cursor and set model to gpt-5.3-codex with reasoning_effort=medium."
        )
    return list(dict.fromkeys(commands))


def validate_plan_content(
    content: str,
    *,
    workspace_root: str | Path | None = None,
    plan_path: str | Path | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    try:
        raw = yaml.safe_load(content)
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            [
                {
                    "code": "yaml.invalid",
                    "message": str(exc),
                    "path": "root",
                    "level": "error",
                }
            ],
            {},
        )

    if not isinstance(raw, dict):
        return (
            False,
            [
                {
                    "code": "yaml.invalid",
                    "message": "plan content must be a YAML object",
                    "path": "root",
                    "level": "error",
                }
            ],
            {},
        )

    version = int(raw.get("version", 1))
    if version != 5:
        issues = [
            {
                "code": "version.invalid",
                "message": UNSUPPORTED_VERSION_MESSAGE,
                "path": "version",
                "level": "error",
            }
        ]
        issues = _dedupe_and_sort_issues(issues)
        return (
            False,
            issues,
            {
                "version": version,
                "expected_version": 5,
                "recommended_commands": _recommended_commands(
                    issues, plan_path=plan_path
                ),
            },
        )

    try:
        plan = PlanSpecV5.model_validate(raw)
    except PydanticValidationError as exc:
        issues = [
            {
                "code": "schema.invalid",
                "message": err["msg"],
                "path": ".".join(str(part) for part in err["loc"]),
                "level": "error",
            }
            for err in exc.errors()
        ]
        issues = _dedupe_and_sort_issues(issues)
        return (
            False,
            issues,
            {
                "recommended_commands": _recommended_commands(
                    issues, plan_path=plan_path
                )
            },
        )

    issues = [asdict(issue) for issue in validate_plan(plan)]
    try:
        compiled = compile_plan(plan)
    except ValidationError as exc:
        issues.extend(asdict(issue) for issue in exc.issues)
        issues = _dedupe_and_sort_issues(issues)
        return (
            False,
            issues,
            {
                "recommended_commands": _recommended_commands(
                    issues, plan_path=plan_path
                )
            },
        )

    parse_issues, runtime, tasks = _append_task_diagnostics(
        plan=plan,
        issues=issues,
    )

    pending_tasks = [task for task in tasks if not task.completed]
    cell_counts = {
        "sequential": 0,
        "parallel": 0,
        "orchestrator": 0,
        "other": 0,
    }
    if runtime is not None:
        for block in runtime.blocks:
            kind = str(block.kind or "other")
            if kind in cell_counts:
                cell_counts[kind] += 1
            else:
                cell_counts["other"] += 1
    tools, mcps = _collect_profile_tools(plan)
    runtime_nodes = len(runtime.nodes) if runtime is not None else 0
    runtime_edges = (
        sum(len(node.depends_on) for node in runtime.nodes)
        if runtime is not None
        else sum(len(parents) for parents in compiled.incoming.values())
    )

    resolved_execution = {
        "template": plan.orchestration.template.value,
        "resolved_cells": [],
        "resolved_nodes": [],
        "task_assignment": {},
        "compile_warnings": [],
    }
    if runtime is not None:
        resolved_execution = {
            "template": plan.orchestration.template.value,
            "resolved_cells": [
                {
                    "id": cell.id,
                    "kind": cell.kind,
                    "lane": cell.lane,
                    "team": cell.team,
                    "behavior_id": cell.behavior_id,
                    "task_ids": list(cell.task_ids),
                    "node_ids": list(cell.node_ids),
                }
                for cell in runtime.resolved_cells
            ],
            "resolved_nodes": [
                {
                    "id": node.id,
                    "cell_id": node.cell_id,
                    "role": node.role,
                    "lane": node.lane,
                    "team": node.team,
                    "block_index": node.block_index,
                    "depends_on": list(node.depends_on),
                    "source_task_id": node.source_task_id,
                    "behavior_id": node.behavior_id,
                    "behavior_kind": node.behavior_kind,
                }
                for node in runtime.nodes
            ],
            "task_assignment": dict(runtime.task_assignment),
            "compile_warnings": list(runtime.compile_warnings),
        }

    summary = {
        "version": 5,
        "plan_id": plan.plan_id,
        "name": plan.name,
        "template": plan.orchestration.template.value,
        "nodes": runtime_nodes,
        "edges": runtime_edges,
        "node_levels": runtime.node_levels
        if runtime is not None
        else compiled.node_levels,
        "groups": runtime.groups if runtime is not None else compiled.groups,
        "required_tools": tools,
        "required_mcps": mcps,
        "phases": 1,
        "parallel_limit": int(plan.constraints.max_parallel),
        "task_counts": {
            "total": len(tasks),
            "pending": len(pending_tasks),
            "completed": len([task for task in tasks if task.completed]),
        },
        "cell_counts": cell_counts,
        "tasks_status": {"status": "ok" if not parse_issues else "issues"},
        "task_parse_issues": parse_issues,
        "recovery_readiness": _git_recovery_readiness(workspace_root),
        "resolved_execution": resolved_execution,
        "recommended_commands": _recommended_commands(issues, plan_path=plan_path),
    }

    issues = _dedupe_and_sort_issues(issues)
    valid = (
        len([issue for issue in issues if issue.get("level", "error") == "error"]) == 0
    )
    return valid, issues, summary


def suggest_fixes(
    plan_data: dict[str, Any], issues: list[dict[str, Any]]
) -> list[ValidationFix]:
    fixes: list[ValidationFix] = []
    tasks = plan_data.get("tasks") if isinstance(plan_data.get("tasks"), list) else []
    agents = (
        plan_data.get("agents") if isinstance(plan_data.get("agents"), list) else []
    )
    agent_ids = [
        str(agent.get("id"))
        for agent in agents
        if isinstance(agent, dict) and agent.get("id")
    ]

    version = (
        int(plan_data.get("version", 0) or 0) if isinstance(plan_data, dict) else 0
    )
    worker_exists = any(
        isinstance(agent, dict) and str(agent.get("role")) == "worker"
        for agent in agents
    )
    orchestrator_exists = any(
        isinstance(agent, dict) and str(agent.get("role")) == "orchestrator"
        for agent in agents
    )
    if version == 5 and not worker_exists:
        fixes.append(
            ValidationFix(
                code="fix.add_default_worker",
                title="Add default worker agent",
                description="Adds a basic worker profile so tasks can execute.",
                path="agents",
                patch={"action": "add_default_worker"},
            )
        )
    if version == 5 and not orchestrator_exists:
        fixes.append(
            ValidationFix(
                code="fix.add_default_orchestrator",
                title="Add default orchestrator agent",
                description="Adds a basic orchestrator profile for orchestration cells.",
                path="agents",
                patch={"action": "add_default_orchestrator"},
            )
        )

    for issue in issues:
        code = str(issue.get("code") or "")
        path = str(issue.get("path") or "")

        if (
            code in {"task.dep_forward", "task.dep_missing"}
            and path.startswith("tasks[")
            and ".deps" in path
        ):
            match = re.match(r"tasks\[(\d+)\]\.deps", path)
            if not match:
                continue
            idx = int(match.group(1))
            if idx < 0 or idx >= len(tasks):
                continue
            task = tasks[idx]
            if not isinstance(task, dict):
                continue
            deps = task.get("deps") if isinstance(task.get("deps"), list) else []
            task_id_to_index = {
                str(row.get("id")): i
                for i, row in enumerate(tasks)
                if isinstance(row, dict) and row.get("id")
            }
            cleaned = [
                str(dep)
                for dep in deps
                if str(dep) in task_id_to_index and task_id_to_index[str(dep)] < idx
            ]
            if cleaned != deps:
                fixes.append(
                    ValidationFix(
                        code="fix.clean_invalid_deps",
                        title="Remove invalid task dependencies",
                        description=f"Removes missing or forward dependencies from {path}.",
                        path=path,
                        patch={"action": "set_value", "path": path, "value": cleaned},
                    )
                )

        if (
            code == "task.unknown_agent"
            and path.startswith("tasks[")
            and ".agent" in path
            and agent_ids
        ):
            fixes.append(
                ValidationFix(
                    code="fix.assign_known_agent",
                    title="Assign a known agent profile",
                    description=f"Replaces {path} with the first available agent id.",
                    path=path,
                    patch={"action": "set_value", "path": path, "value": agent_ids[0]},
                )
            )
        if (
            code == "agent.provider.legacy_openai"
            and path.startswith("agents[")
            and path.endswith(".provider")
        ):
            fixes.append(
                ValidationFix(
                    code="fix.migrate_agent_provider",
                    title="Migrate agent provider to codex",
                    description=f"Updates {path} provider/model defaults for headless codex backend.",
                    path=path,
                    patch={"action": "set_value", "path": path, "value": "codex"},
                )
            )
            model_path = path.rsplit(".", 1)[0] + ".model"
            fixes.append(
                ValidationFix(
                    code="fix.migrate_agent_model",
                    title="Set beta default model",
                    description=f"Sets {model_path} to gpt-5.3-codex.",
                    path=model_path,
                    patch={
                        "action": "set_value",
                        "path": model_path,
                        "value": "gpt-5.3-codex",
                    },
                )
            )
            reasoning_path = path.rsplit(".", 1)[0] + ".reasoning_effort"
            fixes.append(
                ValidationFix(
                    code="fix.migrate_agent_reasoning",
                    title="Set beta default reasoning effort",
                    description=f"Sets {reasoning_path} to medium.",
                    path=reasoning_path,
                    patch={
                        "action": "set_value",
                        "path": reasoning_path,
                        "value": "medium",
                    },
                )
            )

    unique: list[ValidationFix] = []
    seen_keys: set[tuple[str, str]] = set()
    for fix in fixes:
        key = (fix.code, fix.path)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(fix)
    return unique


def apply_fix(plan_data: dict[str, Any], fix: ValidationFix) -> dict[str, Any]:
    updated = dict(plan_data)
    action = str(fix.patch.get("action") if isinstance(fix.patch, dict) else "")

    def _set_by_path(root: dict[str, Any], dotted_path: str, value: Any) -> None:
        cursor: Any = root
        tokens = re.split(r"(\[\d+\])|\.", dotted_path)
        parts = [part for part in tokens if part and part != "."]
        for index, part in enumerate(parts):
            list_match = re.fullmatch(r"\[(\d+)\]", part)
            last = index == len(parts) - 1
            if list_match:
                item_index = int(list_match.group(1))
                if (
                    not isinstance(cursor, list)
                    or item_index < 0
                    or item_index >= len(cursor)
                ):
                    return
                if last:
                    cursor[item_index] = value
                    return
                cursor = cursor[item_index]
                continue
            if "[" in part and part.endswith("]"):
                prefix, _, remainder = part.partition("[")
                idx = int(remainder[:-1])
                if not isinstance(cursor, dict):
                    return
                if prefix not in cursor or not isinstance(cursor[prefix], list):
                    return
                arr = cursor[prefix]
                if idx < 0 or idx >= len(arr):
                    return
                if last:
                    arr[idx] = value
                    return
                cursor = arr[idx]
                continue

            if not isinstance(cursor, dict):
                return
            if last:
                cursor[part] = value
                return
            cursor = cursor.get(part)

    if action == "add_default_worker":
        agents = updated.get("agents")
        if not isinstance(agents, list):
            agents = []
            updated["agents"] = agents
        agents.append(
            {
                "id": "worker_default",
                "role": "worker",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "reasoning_effort": "medium",
                "tools_allow": ["tool:*"],
            }
        )
        return updated

    if action == "add_default_orchestrator":
        agents = updated.get("agents")
        if not isinstance(agents, list):
            agents = []
            updated["agents"] = agents
        agents.append(
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "reasoning_effort": "medium",
                "tools_allow": ["tool:*"],
            }
        )
        return updated

    if action == "set_value":
        path = str(fix.patch.get("path") or fix.path)
        _set_by_path(updated, path, fix.patch.get("value"))
        return updated

    return updated


def issues_by_path(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        key = str(issue.get("path") or "root")
        index.setdefault(key, []).append(issue)
    return index
