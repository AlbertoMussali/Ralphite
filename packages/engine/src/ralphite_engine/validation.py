from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import subprocess
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from ralphite_engine.models import ValidationFix
from ralphite_engine.structure_compiler import RuntimeExecutionPlan, compile_execution_structure
from ralphite_engine.task_parser import parse_task_file
from ralphite_schemas.plan_v2 import PlanSpecV2
from ralphite_schemas.validation import ValidationError, compile_plan, validate_plan


DEPRECATED_V1_MESSAGE = (
    "PlanSpec version 1 is no longer supported. Convert to version 2 with task_source + execution_structure."
)


PlanDocument = PlanSpecV2


def parse_plan_yaml(content: str) -> PlanDocument:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("plan content must be a YAML object")
    version = int(data.get("version", 1))
    if version != 2:
        raise ValueError(DEPRECATED_V1_MESSAGE)
    return PlanSpecV2.model_validate(data)


def resolve_task_source_path(task_source_path: str, workspace_root: str | Path | None) -> Path:
    candidate = Path(task_source_path).expanduser()
    if candidate.is_absolute():
        return candidate
    if workspace_root is not None:
        return (Path(workspace_root).expanduser().resolve() / candidate).resolve()
    return candidate.resolve()


def _collect_profile_tools(plan: PlanSpecV2) -> tuple[list[str], list[str]]:
    tools = sorted(
        {
            entry
            for profile in plan.agent_profiles
            for entry in profile.tools_allow
            if isinstance(entry, str) and entry.startswith("tool:")
        }
    )
    mcps = sorted(
        {
            entry
            for profile in plan.agent_profiles
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
    plan: PlanSpecV2,
    workspace_root: str | Path | None,
    issues: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], RuntimeExecutionPlan | None]:
    task_file_path = resolve_task_source_path(plan.task_source.path, workspace_root)
    if workspace_root is None:
        return {"path": str(task_file_path), "status": "unresolved"}, [], None

    tasks, parse_issues = parse_task_file(task_file_path)
    status = "ok"
    if parse_issues:
        status = "issues"
        for issue in parse_issues:
            issues.append(
                {
                    "code": "task_source.parse_warning",
                    "message": issue,
                    "path": "task_source.path",
                    "level": "warn",
                }
            )

    if not task_file_path.exists():
        status = "missing"
        issues.append(
            {
                "code": "task_source.missing",
                "message": f"task file not found: {task_file_path}",
                "path": "task_source.path",
                "level": "error",
            }
        )

    compiled_runtime, compile_issues = compile_execution_structure(plan, tasks, task_parse_issues=parse_issues)
    for issue in compile_issues:
        issues.append(
            {
                "code": "execution_structure.invalid",
                "message": issue,
                "path": "execution_structure",
                "level": "error",
            }
        )
    return {"path": str(task_file_path), "status": status}, parse_issues, compiled_runtime


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
    if version != 2:
        issues = [
            {
                "code": "version.deprecated_v1",
                "message": DEPRECATED_V1_MESSAGE,
                "path": "version",
                "level": "error",
            }
        ]
        return False, issues, {"version": version, "supported_versions": [2]}

    try:
        plan = PlanSpecV2.model_validate(raw)
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

    task_source_status, parse_issues, runtime = _append_task_diagnostics(
        plan=plan,
        workspace_root=workspace_root,
        issues=issues,
    )
    lane_counts = {
        "seq_pre": sum(len(phase.workers.sequential_before) for phase in plan.execution_structure.phases),
        "parallel": sum(len(phase.workers.parallel) for phase in plan.execution_structure.phases),
        "seq_post": sum(len(phase.workers.sequential_after) for phase in plan.execution_structure.phases),
    }
    tools, mcps = _collect_profile_tools(plan)
    runtime_nodes = len(runtime.nodes) if runtime is not None else 0
    runtime_edges = (
        sum(len(node.depends_on) for node in runtime.nodes)
        if runtime is not None
        else sum(len(parents) for parents in compiled.incoming.values())
    )

    summary = {
        "version": 2,
        "plan_id": plan.plan_id,
        "name": plan.name,
        "nodes": runtime_nodes,
        "edges": runtime_edges,
        "node_levels": runtime.node_levels if runtime is not None else compiled.node_levels,
        "groups": runtime.groups if runtime is not None else compiled.groups,
        "required_tools": tools,
        "required_mcps": mcps,
        "phases": len(plan.execution_structure.phases),
        "lane_counts": lane_counts,
        "orchestrator_defaults": {
            "pre_default_enabled": False,
            "post_default_enabled": True,
        },
        "task_source_status": task_source_status,
        "task_parse_issues": parse_issues,
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
