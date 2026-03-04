from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
import subprocess
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from ralphite_engine.models import ValidationFix
from ralphite_engine.structure_compiler import RuntimeExecutionPlan, compile_execution_structure
from ralphite_engine.task_parser import ParsedTask, parse_plan_tasks
from ralphite_schemas.plan_v4 import PlanSpecV4
from ralphite_schemas.validation import ValidationError, compile_plan, validate_plan


UNSUPPORTED_VERSION_MESSAGE = "Unsupported plan version. Use version 4 unified YAML (tasks + run + agents)."


PlanDocument = PlanSpecV4


def parse_plan_yaml(content: str) -> PlanDocument:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("plan content must be a YAML object")
    version = int(data.get("version", 1))
    if version != 4:
        raise ValueError(UNSUPPORTED_VERSION_MESSAGE)
    return PlanSpecV4.model_validate(data)


def _collect_profile_tools(plan: PlanSpecV4) -> tuple[list[str], list[str]]:
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

    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=False, capture_output=True, text=True)
    dirty = bool(status.stdout.strip())
    return {
        "status": "ready" if not dirty else "dirty",
        "base_branch_clean": not dirty,
        "reason": "working tree has uncommitted changes" if dirty else "ok",
    }


def _append_task_diagnostics(
    *,
    plan: PlanSpecV4,
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

    compiled_runtime, compile_issues = compile_execution_structure(plan, tasks, task_parse_issues=parse_issues)
    for issue in compile_issues:
        issues.append(
            {
                "code": "run.invalid",
                "message": issue,
                "path": "tasks",
                "level": "error",
            }
        )
    return parse_issues, compiled_runtime, tasks


def validate_plan_content(
    content: str,
    *,
    workspace_root: str | Path | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    try:
        raw = yaml.safe_load(content)
    except Exception as exc:  # noqa: BLE001
        return False, [{"code": "yaml.invalid", "message": str(exc), "path": "root", "level": "error"}], {}

    if not isinstance(raw, dict):
        return (
            False,
            [{"code": "yaml.invalid", "message": "plan content must be a YAML object", "path": "root", "level": "error"}],
            {},
        )

    version = int(raw.get("version", 1))
    if version != 4:
        issues = [
            {
                "code": "version.unsupported",
                "message": UNSUPPORTED_VERSION_MESSAGE,
                "path": "version",
                "level": "error",
            }
        ]
        return False, issues, {"version": version, "supported_versions": [4]}

    try:
        plan = PlanSpecV4.model_validate(raw)
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
        return False, issues, {}

    issues = [asdict(issue) for issue in validate_plan(plan)]
    try:
        compiled = compile_plan(plan)
    except ValidationError as exc:
        issues.extend(asdict(issue) for issue in exc.issues)
        return False, issues, {}

    parse_issues, runtime, tasks = _append_task_diagnostics(
        plan=plan,
        issues=issues,
    )

    pending_tasks = [task for task in tasks if not task.completed]
    block_counts = {
        "sequential": len([task for task in pending_tasks if int(task.parallel_group or 0) <= 0]),
        "parallel": len([task for task in pending_tasks if int(task.parallel_group or 0) > 0]),
    }

    group_issues: list[str] = []
    seen: set[int] = set()
    closed: set[int] = set()
    last_group = 0
    for task in pending_tasks:
        grp = int(task.parallel_group or 0)
        if grp > 0:
            if grp < last_group:
                group_issues.append(f"parallel_group {grp} appears after {last_group}; groups must be non-decreasing")
            if grp in closed:
                group_issues.append(f"parallel_group {grp} appears in non-contiguous blocks")
            seen.add(grp)
            last_group = grp
        else:
            if last_group > 0:
                closed.add(last_group)

    for issue in group_issues:
        issues.append(
            {
                "code": "tasks.parallel_group_invalid",
                "message": issue,
                "path": "tasks",
                "level": "error",
            }
        )

    tools, mcps = _collect_profile_tools(plan)
    runtime_nodes = len(runtime.nodes) if runtime is not None else 0
    runtime_edges = (
        sum(len(node.depends_on) for node in runtime.nodes)
        if runtime is not None
        else sum(len(parents) for parents in compiled.incoming.values())
    )

    summary = {
        "version": 4,
        "plan_id": plan.plan_id,
        "name": plan.name,
        "nodes": runtime_nodes,
        "edges": runtime_edges,
        "node_levels": runtime.node_levels if runtime is not None else compiled.node_levels,
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
        "block_counts": block_counts,
        "orchestrator_defaults": {
            "pre_default_enabled": False,
            "post_default_enabled": True,
        },
        "tasks_status": {"status": "ok" if not parse_issues else "issues"},
        "task_parse_issues": parse_issues,
        "task_group_issues": group_issues,
        "recovery_readiness": _git_recovery_readiness(workspace_root),
    }

    valid = len([issue for issue in issues if issue.get("level", "error") == "error"]) == 0
    return valid, issues, summary


def suggest_fixes(plan_data: dict[str, Any], issues: list[dict[str, Any]]) -> list[ValidationFix]:
    fixes: list[ValidationFix] = []
    tasks = plan_data.get("tasks") if isinstance(plan_data.get("tasks"), list) else []
    agents = plan_data.get("agents") if isinstance(plan_data.get("agents"), list) else []
    agent_ids = [str(agent.get("id")) for agent in agents if isinstance(agent, dict) and agent.get("id")]

    version = int(plan_data.get("version", 0) or 0) if isinstance(plan_data, dict) else 0
    worker_exists = any(isinstance(agent, dict) and str(agent.get("role")) == "worker" for agent in agents)
    if version == 4 and not worker_exists:
        fixes.append(
            ValidationFix(
                code="fix.add_default_worker",
                title="Add default worker agent",
                description="Adds a basic worker profile so tasks can execute.",
                path="agents",
                patch={"action": "add_default_worker"},
            )
        )

    for issue in issues:
        code = str(issue.get("code") or "")
        path = str(issue.get("path") or "")

        if code in {"run.pre_orchestrator.unknown_agent", "run.post_orchestrator.unknown_agent"}:
            role = "orchestrator_pre" if "pre_orchestrator" in path else "orchestrator_post"
            replacement = next(
                (
                    str(agent.get("id"))
                    for agent in agents
                    if isinstance(agent, dict) and str(agent.get("role")) == role and agent.get("id")
                ),
                None,
            )
            if replacement:
                fixes.append(
                    ValidationFix(
                        code="fix.rewire_orchestrator_agent",
                        title="Repair orchestrator agent reference",
                        description=f"Points {path} to an existing {role} agent profile.",
                        path=path,
                        patch={"action": "set_value", "path": path, "value": replacement},
                    )
                )

        if code in {"task.dep_forward", "task.dep_missing"} and path.startswith("tasks[") and ".deps" in path:
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

        if code in {
            "tasks.parallel_group_invalid",
            "tasks.parallel_group.non_monotonic",
            "tasks.parallel_group.non_contiguous",
        }:
            normalized: list[int] = []
            current = 0
            seen: dict[int, int] = {}
            for row in tasks:
                if not isinstance(row, dict):
                    normalized.append(0)
                    continue
                raw = int(row.get("parallel_group", 0) or 0)
                if raw <= 0:
                    current = 0
                    normalized.append(0)
                    continue
                if raw not in seen:
                    seen[raw] = max(1, len(seen) + 1)
                mapped = seen[raw]
                if current and mapped < current:
                    mapped = current
                current = mapped
                normalized.append(mapped)
            fixes.append(
                ValidationFix(
                    code="fix.normalize_parallel_groups",
                    title="Normalize parallel group ordering",
                    description="Reassigns parallel groups to contiguous, non-decreasing values.",
                    path="tasks",
                    patch={"action": "normalize_parallel_groups", "values": normalized},
                )
            )
            break

        if code == "task.unknown_agent" and path.startswith("tasks[") and ".agent" in path and agent_ids:
            fixes.append(
                ValidationFix(
                    code="fix.assign_known_agent",
                    title="Assign a known agent profile",
                    description=f"Replaces {path} with the first available agent id.",
                    path=path,
                    patch={"action": "set_value", "path": path, "value": agent_ids[0]},
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
                if not isinstance(cursor, list) or item_index < 0 or item_index >= len(cursor):
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
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "tools_allow": ["tool:*"],
            }
        )
        return updated

    if action == "set_value":
        path = str(fix.patch.get("path") or fix.path)
        _set_by_path(updated, path, fix.patch.get("value"))
        return updated

    if action == "normalize_parallel_groups":
        values = fix.patch.get("values")
        tasks = updated.get("tasks")
        if isinstance(values, list) and isinstance(tasks, list):
            for index, task in enumerate(tasks):
                if not isinstance(task, dict):
                    continue
                if index < len(values):
                    task["parallel_group"] = int(values[index] or 0)
        return updated

    return updated


def issues_by_path(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        key = str(issue.get("path") or "root")
        index.setdefault(key, []).append(issue)
    return index
