from __future__ import annotations

from collections import Counter
import yaml
from dataclasses import asdict
from pydantic import ValidationError as PydanticValidationError

from ralphite_schemas.plan import PlanSpecV1
from ralphite_schemas.validation import ValidationError, compile_plan, validate_plan


def parse_plan_yaml(content: str) -> PlanSpecV1:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("plan content must be a YAML object")
    return PlanSpecV1.model_validate(data)


ISSUE_HINTS = {
    "schema.invalid": "Check required fields and value types in your YAML.",
    "yaml.invalid": "Fix YAML syntax and ensure the document parses to a mapping object.",
    "graph.empty": "Add at least one executable node.",
    "node.agent_missing": "Set agent_id on this node and ensure the agent exists.",
    "node.agent_unknown": "Define this agent in top-level agents before referencing it.",
    "node.gate_missing": "Gate nodes require a gate block with mode and pass_if.",
    "edge.unknown_from": "The edge source node id does not exist.",
    "edge.unknown_to": "The edge target node id does not exist.",
    "edge.retry_missing_loop": "Retry edges must include loop_id.",
    "edge.retry_unknown_loop": "Define the referenced loop in graph.loops.",
    "graph.illegal_cycle": "Only retry-loop cycles are allowed; remove other cycles.",
}


def _summarize_tools(plan: PlanSpecV1) -> tuple[list[str], list[str]]:
    tools: set[str] = set()
    mcps: set[str] = set()
    for agent in plan.agents:
        for item in agent.tools_allow:
            if item.startswith("mcp:"):
                mcps.add(item)
            elif item.startswith("tool:"):
                tools.add(item)
    return sorted(tools), sorted(mcps)


def _build_diagnostics(
    plan: PlanSpecV1 | None,
    summary: dict,
    issues: list[dict],
) -> dict:
    if plan is None:
        return {
            "empty_plan": True,
            "no_agent_nodes": True,
            "no_outputs": True,
            "no_retry_loop": True,
            "single_node_only": True,
            "readable_messages": ["Plan is not readable yet. Fix parsing issues first."],
        }

    nodes = plan.graph.nodes
    empty_plan = len(nodes) == 0
    no_agent_nodes = not any(node.kind.value == "agent" for node in nodes)
    no_outputs = len(plan.outputs.required_artifacts) == 0
    has_retry_edge = any(edge.when.value == "retry" for edge in plan.graph.edges)
    no_retry_loop = not has_retry_edge
    single_node_only = len(nodes) <= 1

    readable_messages: list[str] = []
    if empty_plan:
        readable_messages.append("The graph has no nodes.")
    if no_agent_nodes:
        readable_messages.append("No agent nodes were found.")
    if no_outputs:
        readable_messages.append("No required output artifacts were defined.")
    if no_retry_loop:
        readable_messages.append("No retry loop is configured.")
    if single_node_only:
        readable_messages.append("Only one node exists; consider adding orchestration stages.")
    if not readable_messages and not issues:
        readable_messages.append("Plan looks healthy and ready to run.")

    return {
        "empty_plan": empty_plan,
        "no_agent_nodes": no_agent_nodes,
        "no_outputs": no_outputs,
        "no_retry_loop": no_retry_loop,
        "single_node_only": single_node_only,
        "readable_messages": readable_messages,
    }


def validate_and_compile(content: str) -> tuple[bool, list[dict], dict, dict]:
    plan: PlanSpecV1 | None = None
    try:
        plan = parse_plan_yaml(content)
    except PydanticValidationError as exc:
        issues = [
            {
                "code": "schema.invalid",
                "message": err["msg"],
                "path": ".".join(str(part) for part in err["loc"]),
                "level": "error",
                "hint": ISSUE_HINTS["schema.invalid"],
            }
            for err in exc.errors()
        ]
        return False, issues, {}, _build_diagnostics(None, {}, issues)
    except Exception as exc:  # noqa: BLE001
        issue = {
            "code": "yaml.invalid",
            "message": str(exc),
            "path": "root",
            "level": "error",
            "hint": ISSUE_HINTS["yaml.invalid"],
        }
        return False, [issue], {}, _build_diagnostics(None, {}, [issue])

    issues = []
    for issue in validate_plan(plan):
        data = asdict(issue)
        data["hint"] = ISSUE_HINTS.get(data["code"])
        issues.append(data)

    try:
        compiled = compile_plan(plan)
    except ValidationError as exc:
        issue_dicts = []
        for issue in exc.issues:
            data = asdict(issue)
            data["hint"] = ISSUE_HINTS.get(data["code"])
            issue_dicts.append(data)
        return False, issue_dicts, {}, _build_diagnostics(plan, {}, issue_dicts)

    levels: dict[int, list[str]] = {}
    for node_id, level in compiled.node_levels.items():
        levels.setdefault(level, []).append(node_id)

    kind_counts = Counter(node.kind.value for node in plan.graph.nodes)
    required_tools, required_mcps = _summarize_tools(plan)

    summary = {
        "plan_id": plan.plan_id,
        "name": plan.name,
        "nodes": len(plan.graph.nodes),
        "edges": len(plan.graph.edges),
        "agent_nodes": kind_counts.get("agent", 0),
        "gate_nodes": kind_counts.get("gate", 0),
        "groups": {group: len(nodes) for group, nodes in compiled.groups.items()},
        "loops": [loop.model_dump() for loop in plan.graph.loops],
        "parallel_sets": [{"level": lvl, "nodes": sorted(nodes)} for lvl, nodes in sorted(levels.items())],
        "constraints": plan.constraints.model_dump(mode="json"),
        "required_tools": required_tools,
        "required_mcps": required_mcps,
    }
    valid = not any(issue["level"] == "error" for issue in issues)
    diagnostics = _build_diagnostics(plan, summary, issues)
    return valid, issues, summary, diagnostics
