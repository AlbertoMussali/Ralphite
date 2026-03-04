from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .plan_v4 import PlanSpecV4


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    level: str = "error"


@dataclass(slots=True)
class CompiledPlan:
    plan: PlanSpecV4
    node_levels: dict[str, int]
    outgoing: dict[str, list[str]]
    incoming: dict[str, list[str]]
    groups: dict[str, list[str]]


class ValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("plan validation failed")


def validate_plan(plan: PlanSpecV4) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if plan.version != 4:
        issues.append(ValidationIssue("version.invalid", "version must be 4", "version"))

    if not plan.tasks:
        issues.append(ValidationIssue("tasks.empty", "tasks must contain at least one item", "tasks"))

    agent_ids: set[str] = set()
    role_counts: dict[str, int] = defaultdict(int)
    for idx, agent in enumerate(plan.agents):
        if agent.id in agent_ids:
            issues.append(
                ValidationIssue(
                    "agent.duplicate_id",
                    f"duplicate agent id '{agent.id}'",
                    f"agents[{idx}].id",
                )
            )
        agent_ids.add(agent.id)
        role_counts[agent.role.value] += 1

    if role_counts.get("worker", 0) == 0:
        issues.append(ValidationIssue("agent.missing_worker", "at least one worker agent is required", "agents"))

    if plan.run.pre_orchestrator.enabled and plan.run.pre_orchestrator.agent not in agent_ids:
        issues.append(
            ValidationIssue(
                "run.pre_orchestrator.unknown_agent",
                f"unknown pre orchestrator agent '{plan.run.pre_orchestrator.agent}'",
                "run.pre_orchestrator.agent",
            )
        )
    if plan.run.post_orchestrator.enabled and plan.run.post_orchestrator.agent not in agent_ids:
        issues.append(
            ValidationIssue(
                "run.post_orchestrator.unknown_agent",
                f"unknown post orchestrator agent '{plan.run.post_orchestrator.agent}'",
                "run.post_orchestrator.agent",
            )
        )

    task_ids: set[str] = set()
    first_seen_group_index: dict[int, int] = {}
    last_group: int | None = None
    closed_groups: set[int] = set()

    for idx, task in enumerate(plan.tasks):
        path_prefix = f"tasks[{idx}]"
        if task.id in task_ids:
            issues.append(ValidationIssue("task.duplicate_id", f"duplicate task id '{task.id}'", f"{path_prefix}.id"))
        task_ids.add(task.id)

        if task.agent and task.agent not in agent_ids:
            issues.append(
                ValidationIssue(
                    "task.unknown_agent",
                    f"task '{task.id}' references unknown agent '{task.agent}'",
                    f"{path_prefix}.agent",
                )
            )

        for dep in task.deps:
            if dep == task.id:
                issues.append(ValidationIssue("task.self_dep", f"task '{task.id}' has self dependency", f"{path_prefix}.deps"))

        group = int(task.parallel_group or 0)
        if group > 0:
            if last_group is None:
                last_group = group
                first_seen_group_index.setdefault(group, idx)
            else:
                if group < last_group and group not in closed_groups:
                    issues.append(
                        ValidationIssue(
                            "tasks.parallel_group.non_monotonic",
                            f"parallel_group {group} appears after group {last_group}; groups must be non-decreasing by first appearance",
                            f"{path_prefix}.parallel_group",
                        )
                    )
                if group != last_group:
                    closed_groups.add(last_group)
                    if group in closed_groups:
                        issues.append(
                            ValidationIssue(
                                "tasks.parallel_group.non_contiguous",
                                f"parallel_group {group} appears in non-contiguous blocks",
                                f"{path_prefix}.parallel_group",
                            )
                        )
                    last_group = group
                    first_seen_group_index.setdefault(group, idx)
        else:
            if last_group is not None:
                closed_groups.add(last_group)

    ordered_ids = [task.id for task in plan.tasks]
    position = {task_id: idx for idx, task_id in enumerate(ordered_ids)}
    for idx, task in enumerate(plan.tasks):
        for dep in task.deps:
            if dep not in position:
                issues.append(
                    ValidationIssue(
                        "task.dep_missing",
                        f"task '{task.id}' depends on missing task '{dep}'",
                        f"tasks[{idx}].deps",
                    )
                )
                continue
            if position[dep] >= idx:
                issues.append(
                    ValidationIssue(
                        "task.dep_forward",
                        f"task '{task.id}' has forward dependency '{dep}'",
                        f"tasks[{idx}].deps",
                    )
                )

    return issues


def _compile_plan(plan: PlanSpecV4) -> CompiledPlan:
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    groups: dict[str, list[str]] = defaultdict(list)

    def add_node(node_id: str, group: str, depends_on: list[str]) -> None:
        groups[group].append(node_id)
        for dep in depends_on:
            incoming[node_id].append(dep)
            outgoing[dep].append(node_id)
        incoming.setdefault(node_id, incoming.get(node_id, []))
        outgoing.setdefault(node_id, outgoing.get(node_id, []))

    anchor: list[str] = []
    if plan.run.pre_orchestrator.enabled:
        pre_id = "phase-1::orchestrator_pre"
        add_node(pre_id, "phase-1", anchor)
        anchor = [pre_id]

    for task in plan.tasks:
        if task.completed:
            continue
        task_node_id = f"phase-1::task::{task.id}"
        add_node(task_node_id, "phase-1", list(anchor))
        if int(task.parallel_group or 0) > 0:
            # Parallel tasks in the same group share the same anchor.
            pass
        else:
            anchor = [task_node_id]

    if plan.run.post_orchestrator.enabled:
        post_id = "phase-1::orchestrator_post"
        add_node(post_id, "phase-1", list(anchor))

    indegree = {node_id: len(parents) for node_id, parents in incoming.items()}
    q = deque([nid for nid, deg in indegree.items() if deg == 0])
    topo: list[str] = []
    while q:
        node_id = q.popleft()
        topo.append(node_id)
        for nxt in outgoing.get(node_id, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                q.append(nxt)

    if len(topo) != len(indegree):
        raise ValidationError([ValidationIssue("tasks.illegal_cycle", "tasks introduce a cycle", "tasks")])

    node_levels: dict[str, int] = {}
    for node_id in topo:
        parents = incoming.get(node_id, [])
        if not parents:
            node_levels[node_id] = 0
        else:
            node_levels[node_id] = max(node_levels[parent] + 1 for parent in parents)

    return CompiledPlan(
        plan=plan,
        node_levels=node_levels,
        outgoing=dict(outgoing),
        incoming=dict(incoming),
        groups=dict(groups),
    )


def compile_plan(plan: PlanSpecV4) -> CompiledPlan:
    issues = validate_plan(plan)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise ValidationError(errors)
    return _compile_plan(plan)
