from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .plan import PlanSpecV1


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    level: str = "error"


@dataclass(slots=True)
class CompiledPlan:
    plan: PlanSpecV1
    node_levels: dict[str, int]
    outgoing: dict[str, list[str]]
    incoming: dict[str, list[str]]
    groups: dict[str, list[str]]


class ValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("plan validation failed")


def validate_plan(plan: PlanSpecV1) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if plan.version != 1:
        issues.append(ValidationIssue("version.invalid", "version must be 1", "version"))

    node_by_id = {}
    for idx, node in enumerate(plan.graph.nodes):
        if node.id in node_by_id:
            issues.append(
                ValidationIssue("node.duplicate_id", f"duplicate node id '{node.id}'", f"graph.nodes[{idx}].id")
            )
        node_by_id[node.id] = node

    if not plan.graph.nodes:
        issues.append(ValidationIssue("graph.empty", "graph must contain at least one node", "graph.nodes"))

    agent_ids = {agent.id for agent in plan.agents}
    for idx, node in enumerate(plan.graph.nodes):
        if node.kind == "agent" and not node.agent_id:
            issues.append(
                ValidationIssue("node.agent_missing", "agent node requires agent_id", f"graph.nodes[{idx}].agent_id")
            )
        if node.kind == "agent" and node.agent_id and node.agent_id not in agent_ids:
            issues.append(
                ValidationIssue(
                    "node.agent_unknown",
                    f"unknown agent_id '{node.agent_id}'",
                    f"graph.nodes[{idx}].agent_id",
                )
            )
        if node.kind == "gate" and not node.gate:
            issues.append(
                ValidationIssue("node.gate_missing", "gate node requires gate config", f"graph.nodes[{idx}].gate")
            )

    loop_ids = {loop.id for loop in plan.graph.loops}
    seen_loops: set[str] = set()
    for idx, loop in enumerate(plan.graph.loops):
        if loop.id in seen_loops:
            issues.append(
                ValidationIssue("loop.duplicate_id", f"duplicate loop id '{loop.id}'", f"graph.loops[{idx}].id")
            )
        seen_loops.add(loop.id)

    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {node.id: 0 for node in plan.graph.nodes}
    retry_edges: list[tuple[str, str]] = []

    for idx, edge in enumerate(plan.graph.edges):
        if edge.from_node not in node_by_id:
            issues.append(
                ValidationIssue(
                    "edge.unknown_from", f"unknown edge source '{edge.from_node}'", f"graph.edges[{idx}].from"
                )
            )
            continue
        if edge.to not in node_by_id:
            issues.append(
                ValidationIssue("edge.unknown_to", f"unknown edge target '{edge.to}'", f"graph.edges[{idx}].to")
            )
            continue

        if edge.when == "retry":
            retry_edges.append((edge.from_node, edge.to))
            if not edge.loop_id:
                issues.append(
                    ValidationIssue("edge.retry_missing_loop", "retry edge requires loop_id", f"graph.edges[{idx}].loop_id")
                )
            elif edge.loop_id not in loop_ids:
                issues.append(
                    ValidationIssue(
                        "edge.retry_unknown_loop",
                        f"retry edge references unknown loop '{edge.loop_id}'",
                        f"graph.edges[{idx}].loop_id",
                    )
                )

        if edge.when != "retry":
            outgoing[edge.from_node].append(edge.to)
            indegree[edge.to] += 1

    # DAG check ignoring retry edges (declared loops).
    q = deque([nid for nid, deg in indegree.items() if deg == 0])
    visited = 0
    while q:
        nid = q.popleft()
        visited += 1
        for nxt in outgoing.get(nid, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                q.append(nxt)

    if visited != len(plan.graph.nodes):
        issues.append(
            ValidationIssue(
                "graph.illegal_cycle",
                "graph has cycle outside declared retry loops",
                "graph.edges",
            )
        )

    return issues


def compile_plan(plan: PlanSpecV1) -> CompiledPlan:
    issues = validate_plan(plan)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise ValidationError(errors)

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)

    for edge in plan.graph.edges:
        outgoing[edge.from_node].append(edge.to)
        incoming[edge.to].append(edge.from_node)

    memo: dict[str, int] = {}

    def level_for(node_id: str, stack: set[str]) -> int:
        if node_id in memo:
            return memo[node_id]
        if node_id in stack:
            # retry loops can route back; collapse cycle level.
            return 0
        stack.add(node_id)
        prevs = incoming.get(node_id, [])
        if not prevs:
            lvl = 0
        else:
            lvl = max(level_for(prev, stack) + 1 for prev in prevs)
        stack.remove(node_id)
        memo[node_id] = lvl
        return lvl

    node_levels = {node.id: level_for(node.id, set()) for node in plan.graph.nodes}
    groups: dict[str, list[str]] = defaultdict(list)
    for node in plan.graph.nodes:
        groups[node.group].append(node.id)

    return CompiledPlan(
        plan=plan,
        node_levels=node_levels,
        outgoing=dict(outgoing),
        incoming=dict(incoming),
        groups=dict(groups),
    )
