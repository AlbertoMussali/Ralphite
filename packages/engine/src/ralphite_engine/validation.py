from __future__ import annotations

from dataclasses import asdict
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from ralphite_engine.models import ValidationFix
from ralphite_schemas.plan import PlanSpecV1
from ralphite_schemas.validation import ValidationError, compile_plan, validate_plan


def parse_plan_yaml(content: str) -> PlanSpecV1:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("plan content must be a YAML object")
    return PlanSpecV1.model_validate(data)


def validate_plan_content(content: str) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    try:
        plan = parse_plan_yaml(content)
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
    except Exception as exc:  # noqa: BLE001
        return False, [{"code": "yaml.invalid", "message": str(exc), "path": "root", "level": "error"}], {}

    issues = [asdict(issue) for issue in validate_plan(plan)]
    try:
        compiled = compile_plan(plan)
    except ValidationError as exc:
        issues.extend(asdict(issue) for issue in exc.issues)
        return False, issues, {}

    summary = {
        "plan_id": plan.plan_id,
        "name": plan.name,
        "nodes": len(plan.graph.nodes),
        "edges": len(plan.graph.edges),
        "node_levels": compiled.node_levels,
        "groups": compiled.groups,
        "required_tools": sorted(
            {
                entry
                for agent in plan.agents
                for entry in agent.tools_allow
                if isinstance(entry, str) and entry.startswith("tool:")
            }
        ),
        "required_mcps": sorted(
            {
                entry
                for agent in plan.agents
                for entry in agent.tools_allow
                if isinstance(entry, str) and entry.startswith("mcp:")
            }
        ),
    }
    return len([issue for issue in issues if issue.get("level", "error") == "error"]) == 0, issues, summary


def suggest_fixes(plan_data: dict[str, Any], issues: list[dict[str, Any]]) -> list[ValidationFix]:
    fixes: list[ValidationFix] = []
    graph = plan_data.setdefault("graph", {})
    nodes = graph.setdefault("nodes", [])
    agents = plan_data.setdefault("agents", [])

    for issue in issues:
        code = issue.get("code", "")
        path = str(issue.get("path", ""))

        if code == "graph.empty":
            fixes.append(
                ValidationFix(
                    code=code,
                    title="Add default worker node",
                    description="Insert a basic executable agent node so plan can run.",
                    path=path,
                    patch={
                        "op": "add_default_node",
                        "node": {
                            "id": "n1",
                            "kind": "agent",
                            "group": "execution",
                            "agent_id": "worker",
                            "task": "Execute work item",
                            "depends_on": [],
                        },
                    },
                )
            )

        if code in {"node.agent_missing", "node.agent_unknown"}:
            fixes.append(
                ValidationFix(
                    code=code,
                    title="Repair missing/unknown agent",
                    description="Assign first defined agent id or create a worker agent stub.",
                    path=path,
                    patch={"op": "repair_agent_reference"},
                )
            )

        if code == "node.gate_missing":
            fixes.append(
                ValidationFix(
                    code=code,
                    title="Add default gate config",
                    description="Provide default gate mode and pass condition.",
                    path=path,
                    patch={"op": "add_gate_defaults"},
                )
            )

        if code == "edge.retry_missing_loop":
            fixes.append(
                ValidationFix(
                    code=code,
                    title="Attach retry loop id",
                    description="Set loop_id to main_loop and add loop if missing.",
                    path=path,
                    patch={"op": "add_retry_loop_id", "loop_id": "main_loop"},
                )
            )

    # Heuristic fix suggestions when no explicit code matched.
    if not fixes and not agents:
        fixes.append(
            ValidationFix(
                code="agents.empty",
                title="Add worker agent",
                description="Create a default worker agent for quick recovery.",
                path="agents",
                patch={"op": "add_default_agent"},
            )
        )

    return fixes


def apply_fix(plan_data: dict[str, Any], fix: ValidationFix) -> dict[str, Any]:
    graph = plan_data.setdefault("graph", {})
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    loops = graph.setdefault("loops", [])
    agents = plan_data.setdefault("agents", [])

    op = fix.patch.get("op")
    if op == "add_default_node":
        if not nodes:
            nodes.append(fix.patch["node"])

    elif op == "repair_agent_reference":
        if not agents:
            agents.append(
                {
                    "id": "worker",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "system_prompt": "Execute assigned tasks.",
                    "tools_allow": ["tool:*", "mcp:*"],
                }
            )
        first_agent = agents[0]["id"]
        for node in nodes:
            if node.get("kind") == "agent" and not node.get("agent_id"):
                node["agent_id"] = first_agent

    elif op == "add_gate_defaults":
        for node in nodes:
            if node.get("kind") == "gate" and not isinstance(node.get("gate"), dict):
                node["gate"] = {"mode": "rubric", "pass_if": "all_acceptance_checks_pass"}

    elif op == "add_retry_loop_id":
        loop_id = str(fix.patch.get("loop_id", "main_loop"))
        for edge in edges:
            if edge.get("when") == "retry" and not edge.get("loop_id"):
                edge["loop_id"] = loop_id
        if loop_id not in {loop.get("id") for loop in loops if isinstance(loop, dict)}:
            loops.append({"id": loop_id, "max_iterations": 3})

    elif op == "add_default_agent":
        if not agents:
            agents.append(
                {
                    "id": "worker",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "system_prompt": "Execute assigned tasks.",
                    "tools_allow": ["tool:*", "mcp:*"],
                }
            )

    return plan_data
