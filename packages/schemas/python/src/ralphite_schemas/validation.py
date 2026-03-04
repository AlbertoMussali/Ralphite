from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .plan_v5 import CustomCellKind, OrchestrationTemplate, PlanSpecV5


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    level: str = "error"


@dataclass(slots=True)
class CompiledPlan:
    plan: PlanSpecV5
    node_levels: dict[str, int]
    outgoing: dict[str, list[str]]
    incoming: dict[str, list[str]]
    groups: dict[str, list[str]]


class ValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("plan validation failed")


def _task_is_branched_mapped(task_lane: str | None, task_group: str | None, task_cell: str | None) -> bool:
    if task_lane:
        return True
    if task_group and task_group.lower() == "trunk":
        return True
    if task_cell:
        return True
    return False


def validate_plan(plan: PlanSpecV5) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if plan.version != 5:
        issues.append(ValidationIssue("version.invalid", "version must be 5", "version"))

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
    if role_counts.get("orchestrator", 0) == 0:
        issues.append(ValidationIssue("agent.missing_orchestrator", "at least one orchestrator agent is required", "agents"))

    behavior_ids: set[str] = set()
    for idx, behavior in enumerate(plan.orchestration.behaviors):
        path_prefix = f"orchestration.behaviors[{idx}]"
        if behavior.id in behavior_ids:
            issues.append(
                ValidationIssue(
                    "orchestration.behavior.duplicate_id",
                    f"duplicate behavior id '{behavior.id}'",
                    f"{path_prefix}.id",
                )
            )
        behavior_ids.add(behavior.id)
        if behavior.agent and behavior.agent not in agent_ids:
            issues.append(
                ValidationIssue(
                    "orchestration.behavior.unknown_agent",
                    f"behavior '{behavior.id}' references unknown agent '{behavior.agent}'",
                    f"{path_prefix}.agent",
                )
            )

    task_ids: set[str] = set()
    position: dict[str, int] = {}
    pending_task_ids: list[str] = []
    for idx, task in enumerate(plan.tasks):
        path_prefix = f"tasks[{idx}]"
        if task.id in task_ids:
            issues.append(ValidationIssue("task.duplicate_id", f"duplicate task id '{task.id}'", f"{path_prefix}.id"))
        task_ids.add(task.id)
        position[task.id] = idx
        if not task.completed:
            pending_task_ids.append(task.id)

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

    if plan.orchestration.template == OrchestrationTemplate.BRANCHED:
        known_lanes = set(plan.orchestration.branched.lanes)
        if not known_lanes:
            issues.append(
                ValidationIssue(
                    "orchestration.branched.lanes_empty",
                    "branched template requires at least one lane in orchestration.branched.lanes",
                    "orchestration.branched.lanes",
                )
            )
        for idx, task in enumerate(plan.tasks):
            if task.completed:
                continue
            lane = (task.routing.lane or "").strip() or None
            group = (task.routing.group or "").strip() or None
            cell = (task.routing.cell or "").strip() or None
            if not _task_is_branched_mapped(lane, group, cell):
                issues.append(
                    ValidationIssue(
                        "tasks.unassigned",
                        f"task '{task.id}' is not mapped for branched orchestration; set routing.lane, routing.group='trunk', or routing.cell",
                        f"tasks[{idx}].routing",
                    )
                )
            if lane and lane not in known_lanes:
                issues.append(
                    ValidationIssue(
                        "tasks.routing.unknown_lane",
                        f"task '{task.id}' references unknown lane '{lane}'",
                        f"tasks[{idx}].routing.lane",
                    )
                )

    if plan.orchestration.template == OrchestrationTemplate.CUSTOM:
        if not plan.orchestration.custom.cells:
            issues.append(
                ValidationIssue(
                    "orchestration.custom.cells_empty",
                    "custom template requires orchestration.custom.cells",
                    "orchestration.custom.cells",
                )
            )
        cell_ids: set[str] = set()
        for idx, cell in enumerate(plan.orchestration.custom.cells):
            path_prefix = f"orchestration.custom.cells[{idx}]"
            if cell.id in cell_ids:
                issues.append(
                    ValidationIssue(
                        "orchestration.custom.duplicate_cell_id",
                        f"duplicate custom cell id '{cell.id}'",
                        f"{path_prefix}.id",
                    )
                )
            cell_ids.add(cell.id)
            if cell.kind == CustomCellKind.ORCHESTRATOR and cell.behavior and cell.behavior not in behavior_ids:
                issues.append(
                    ValidationIssue(
                        "orchestration.custom.unknown_behavior",
                        f"custom cell '{cell.id}' references unknown behavior '{cell.behavior}'",
                        f"{path_prefix}.behavior",
                    )
                )
            for dep in cell.depends_on:
                if dep not in cell_ids:
                    issues.append(
                        ValidationIssue(
                            "orchestration.custom.dep_unknown",
                            f"custom cell '{cell.id}' depends on unknown or forward cell '{dep}'",
                            f"{path_prefix}.depends_on",
                        )
                    )

    for idx, task in enumerate(plan.tasks):
        if plan.orchestration.template != OrchestrationTemplate.GENERAL_SPS and not task.completed:
            # For non-SPS templates, require at least one routing signal unless template-level mapping exists.
            if not (task.routing.lane or task.routing.cell or task.routing.group):
                issues.append(
                    ValidationIssue(
                        "tasks.routing.missing",
                        f"task '{task.id}' is missing routing metadata for template '{plan.orchestration.template.value}'",
                        f"tasks[{idx}].routing",
                    )
                )

    return issues


def _compile_plan(plan: PlanSpecV5) -> CompiledPlan:
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    groups: dict[str, list[str]] = defaultdict(list)

    task_ids = [task.id for task in plan.tasks if not task.completed]
    for task in plan.tasks:
        if task.completed:
            continue
        node_id = f"task::{task.id}"
        group = task.routing.lane or task.routing.group or "phase-1"
        groups[group].append(node_id)
        incoming.setdefault(node_id, [])
        outgoing.setdefault(node_id, [])
        for dep in task.deps:
            dep_id = f"task::{dep}"
            if dep in task_ids:
                incoming[node_id].append(dep_id)
                outgoing[dep_id].append(node_id)

    indegree = {node_id: len(parents) for node_id, parents in incoming.items()}
    queue = deque([nid for nid, degree in indegree.items() if degree == 0])
    topo: list[str] = []
    while queue:
        node_id = queue.popleft()
        topo.append(node_id)
        for nxt in outgoing.get(node_id, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

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


def compile_plan(plan: PlanSpecV5) -> CompiledPlan:
    issues = validate_plan(plan)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise ValidationError(errors)
    return _compile_plan(plan)
