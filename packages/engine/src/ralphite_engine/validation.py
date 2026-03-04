from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
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
    del plan_data
    del issues
    return []


def apply_fix(plan_data: dict[str, Any], fix: ValidationFix) -> dict[str, Any]:
    del fix
    return plan_data


def issues_by_path(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        key = str(issue.get("path") or "root")
        index.setdefault(key, []).append(issue)
    return index
