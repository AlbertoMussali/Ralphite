from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .plan_v2 import PlanSpecV2


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    level: str = "error"


@dataclass(slots=True)
class CompiledPlan:
    plan: PlanSpecV2
    node_levels: dict[str, int]
    outgoing: dict[str, list[str]]
    incoming: dict[str, list[str]]
    groups: dict[str, list[str]]


class ValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("plan validation failed")


def validate_plan(plan: PlanSpecV2) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if plan.version != 2:
        issues.append(ValidationIssue("version.invalid", "version must be 2", "version"))

    if plan.task_source.parser_version != 2:
        issues.append(
            ValidationIssue(
                "task_source.parser_version_invalid",
                "task_source.parser_version must be 2",
                "task_source.parser_version",
            )
        )

    if not plan.execution_structure.phases:
        issues.append(
            ValidationIssue(
                "execution_structure.empty",
                "execution_structure.phases must contain at least one phase",
                "execution_structure.phases",
            )
        )

    profile_ids: set[str] = set()
    role_counts: dict[str, int] = defaultdict(int)
    for idx, profile in enumerate(plan.agent_profiles):
        if profile.id in profile_ids:
            issues.append(
                ValidationIssue(
                    "agent_profile.duplicate_id",
                    f"duplicate agent profile id '{profile.id}'",
                    f"agent_profiles[{idx}].id",
                )
            )
        profile_ids.add(profile.id)
        role_counts[profile.role.value] += 1

    if role_counts.get("worker", 0) == 0:
        issues.append(
            ValidationIssue(
                "agent_profile.missing_worker",
                "at least one worker agent_profile is required",
                "agent_profiles",
            )
        )

    seen_phases: set[str] = set()
    selected_tasks: set[str] = set()
    for idx, phase in enumerate(plan.execution_structure.phases):
        path_prefix = f"execution_structure.phases[{idx}]"
        if phase.id in seen_phases:
            issues.append(ValidationIssue("phase.duplicate_id", f"duplicate phase id '{phase.id}'", f"{path_prefix}.id"))
        seen_phases.add(phase.id)

        if phase.pre_orchestrator.enabled and phase.pre_orchestrator.agent_profile_id not in profile_ids:
            issues.append(
                ValidationIssue(
                    "phase.pre_orchestrator.unknown_profile",
                    f"unknown pre_orchestrator profile '{phase.pre_orchestrator.agent_profile_id}'",
                    f"{path_prefix}.pre_orchestrator.agent_profile_id",
                )
            )

        if phase.post_orchestrator.enabled and phase.post_orchestrator.agent_profile_id not in profile_ids:
            issues.append(
                ValidationIssue(
                    "phase.post_orchestrator.unknown_profile",
                    f"unknown post_orchestrator profile '{phase.post_orchestrator.agent_profile_id}'",
                    f"{path_prefix}.post_orchestrator.agent_profile_id",
                )
            )

        lane_pairs = [
            ("sequential_before", phase.workers.sequential_before),
            ("parallel", phase.workers.parallel),
            ("sequential_after", phase.workers.sequential_after),
        ]
        local_selected: set[str] = set()
        for lane_name, task_ids in lane_pairs:
            for task_id in task_ids:
                if task_id in local_selected:
                    issues.append(
                        ValidationIssue(
                            "phase.task_duplicate",
                            f"task '{task_id}' appears multiple times in phase '{phase.id}'",
                            f"{path_prefix}.workers.{lane_name}",
                        )
                    )
                local_selected.add(task_id)
                if task_id in selected_tasks:
                    issues.append(
                        ValidationIssue(
                            "phase.task_reused",
                            f"task '{task_id}' is assigned in multiple phases",
                            f"{path_prefix}.workers.{lane_name}",
                        )
                    )
                selected_tasks.add(task_id)

    return issues


def _compile_plan(plan: PlanSpecV2) -> CompiledPlan:
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

    previous_phase_terminal: list[str] = []
    for phase in plan.execution_structure.phases:
        current_anchor: list[str] = list(previous_phase_terminal)

        if phase.pre_orchestrator.enabled:
            pre_id = f"{phase.id}::orchestrator_pre"
            add_node(pre_id, phase.id, current_anchor)
            current_anchor = [pre_id]

        seq_before_ids = [f"{phase.id}::seq_pre::{task_id}" for task_id in phase.workers.sequential_before]
        for node_id in seq_before_ids:
            add_node(node_id, phase.id, list(current_anchor))
            current_anchor = [node_id]

        parallel_ids = [f"{phase.id}::parallel::{task_id}" for task_id in phase.workers.parallel]
        if parallel_ids:
            parallel_anchor = list(current_anchor)
            for node_id in parallel_ids:
                add_node(node_id, phase.id, parallel_anchor)
            current_anchor = list(parallel_ids)

        seq_after_ids = [f"{phase.id}::seq_post::{task_id}" for task_id in phase.workers.sequential_after]
        for node_id in seq_after_ids:
            add_node(node_id, phase.id, list(current_anchor))
            current_anchor = [node_id]

        if phase.post_orchestrator.enabled:
            post_id = f"{phase.id}::orchestrator_post"
            add_node(post_id, phase.id, list(current_anchor))
            current_anchor = [post_id]

        previous_phase_terminal = current_anchor
        groups.setdefault(phase.id, groups.get(phase.id, []))

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
        raise ValidationError(
            [
                ValidationIssue(
                    "execution_structure.illegal_cycle",
                    "execution_structure introduces a cycle",
                    "execution_structure.phases",
                )
            ]
        )

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


def compile_plan(plan: PlanSpecV2) -> CompiledPlan:
    issues = validate_plan(plan)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise ValidationError(errors)
    return _compile_plan(plan)
