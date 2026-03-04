from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from ralphite_schemas.plan_v2 import PlanSpecV2

from .task_parser import ParsedTask


@dataclass(slots=True)
class RuntimeNodeSpec:
    id: str
    kind: str
    group: str
    depends_on: list[str]
    task: str
    agent_profile_id: str
    role: str
    phase: str
    lane: str
    source_task_id: str | None = None


@dataclass(slots=True)
class RuntimeExecutionPlan:
    nodes: list[RuntimeNodeSpec]
    node_payload: dict[str, dict[str, Any]]
    node_levels: dict[str, int]
    groups: dict[str, list[str]]
    parallel_limit: int
    task_parse_issues: list[str]


def _normalize_phase_key(value: str) -> str:
    return value.strip() or "phase-1"


def compile_execution_structure(
    plan: PlanSpecV2,
    tasks: list[ParsedTask],
    *,
    task_parse_issues: list[str] | None = None,
) -> tuple[RuntimeExecutionPlan | None, list[str]]:
    issues: list[str] = []
    phase_rank = {phase.id: idx for idx, phase in enumerate(plan.execution_structure.phases)}

    pending_tasks = [task for task in tasks if not task.completed]
    tasks_by_id = {task.id: task for task in pending_tasks}
    duplicate_count = len(pending_tasks) - len(tasks_by_id)
    if duplicate_count > 0:
        issues.append(f"duplicate pending task ids detected: {duplicate_count}")
    profile_ids = {profile.id for profile in plan.agent_profiles}

    task_index_by_phase_lane: dict[tuple[str, str], list[ParsedTask]] = defaultdict(list)
    for task in pending_tasks:
        task_index_by_phase_lane[(_normalize_phase_key(task.phase), task.lane)].append(task)
    for key in task_index_by_phase_lane:
        task_index_by_phase_lane[key].sort(key=lambda row: row.line_no)

    nodes: list[RuntimeNodeSpec] = []
    groups: dict[str, list[str]] = defaultdict(list)
    task_to_node_id: dict[str, str] = {}
    previous_phase_terminal: list[str] = []

    def add_node(node: RuntimeNodeSpec) -> None:
        nodes.append(node)
        groups[node.group].append(node.id)

    def select_tasks(phase_id: str, lane: str, requested: list[str]) -> list[ParsedTask]:
        if requested:
            selected: list[ParsedTask] = []
            for task_id in requested:
                task = tasks_by_id.get(task_id)
                if not task:
                    issues.append(f"phase '{phase_id}' lane '{lane}' references unknown or completed task '{task_id}'")
                    continue
                selected.append(task)
            return selected
        return list(task_index_by_phase_lane.get((_normalize_phase_key(phase_id), lane), []))

    for phase in plan.execution_structure.phases:
        phase_id = _normalize_phase_key(phase.id)
        anchor = list(previous_phase_terminal)

        if phase.pre_orchestrator.enabled:
            pre_node = RuntimeNodeSpec(
                id=f"{phase_id}::orchestrator_pre",
                kind="agent",
                group=phase_id,
                depends_on=list(anchor),
                task=f"Prepare phase '{phase_id}' execution order and workspace state before workers run.",
                agent_profile_id=phase.pre_orchestrator.agent_profile_id,
                role="orchestrator_pre",
                phase=phase_id,
                lane="orchestrator_pre",
            )
            add_node(pre_node)
            anchor = [pre_node.id]

        seq_pre_tasks = select_tasks(phase_id, "seq_pre", phase.workers.sequential_before)
        parallel_tasks = select_tasks(phase_id, "parallel", phase.workers.parallel)
        seq_post_tasks = select_tasks(phase_id, "seq_post", phase.workers.sequential_after)

        def add_worker_task(task: ParsedTask, lane: str, depends_on: list[str]) -> str:
            node_id = f"{phase_id}::{lane}::{task.id}"
            if task.id in task_to_node_id:
                issues.append(f"task '{task.id}' is assigned to multiple phase lanes")
            if task.agent_profile not in profile_ids:
                issues.append(f"task '{task.id}' references unknown agent_profile '{task.agent_profile}'")
            task_to_node_id[task.id] = node_id
            node = RuntimeNodeSpec(
                id=node_id,
                kind="agent",
                group=phase_id,
                depends_on=list(depends_on),
                task=task.description,
                agent_profile_id=task.agent_profile,
                role="worker",
                phase=phase_id,
                lane=lane,
                source_task_id=task.id,
            )
            add_node(node)
            return node_id

        for task in seq_pre_tasks:
            node_id = add_worker_task(task, "seq_pre", anchor)
            anchor = [node_id]

        parallel_ids: list[str] = []
        parallel_anchor = list(anchor)
        for task in parallel_tasks:
            node_id = add_worker_task(task, "parallel", parallel_anchor)
            parallel_ids.append(node_id)
        if parallel_ids:
            anchor = list(parallel_ids)

        for idx, task in enumerate(seq_post_tasks):
            deps = anchor if idx == 0 else [anchor[-1]]
            node_id = add_worker_task(task, "seq_post", deps)
            anchor = [node_id]

        if phase.post_orchestrator.enabled:
            post_node = RuntimeNodeSpec(
                id=f"{phase_id}::orchestrator_post",
                kind="agent",
                group=phase_id,
                depends_on=list(anchor),
                task=(
                    f"Integrate all phase '{phase_id}' worker outputs to main while preserving worker commits, "
                    "clean temporary artifacts/worktrees, and summarize results for the user."
                ),
                agent_profile_id=phase.post_orchestrator.agent_profile_id,
                role="orchestrator_post",
                phase=phase_id,
                lane="orchestrator_post",
            )
            add_node(post_node)
            anchor = [post_node.id]

        previous_phase_terminal = list(anchor)

    node_by_id = {node.id: node for node in nodes}
    for node in nodes:
        if node.role != "worker" or not node.source_task_id:
            continue
        task = tasks_by_id.get(node.source_task_id)
        if not task:
            continue
        for dep_task_id in task.depends_on:
            dep_node_id = task_to_node_id.get(dep_task_id)
            if not dep_node_id:
                issues.append(
                    f"task '{task.id}' depends_on '{dep_task_id}' but dependency is not selected in execution_structure"
                )
                continue
            if dep_node_id == node.id:
                issues.append(f"task '{task.id}' has self dependency")
                continue
            dep_node = node_by_id.get(dep_node_id)
            if dep_node and phase_rank.get(dep_node.phase, -1) > phase_rank.get(node.phase, -1):
                issues.append(
                    f"task '{task.id}' depends on '{dep_task_id}' from a later phase; cross-phase backwards dependencies are not allowed"
                )
                continue
            if dep_node_id not in node.depends_on:
                node.depends_on.append(dep_node_id)

    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {node.id: 0 for node in nodes}
    for node in nodes:
        for dep in node.depends_on:
            if dep not in node_by_id:
                issues.append(f"node '{node.id}' depends on missing node '{dep}'")
                continue
            outgoing[dep].append(node.id)
            indegree[node.id] += 1

    queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
    topo: list[str] = []
    while queue:
        current = queue.popleft()
        topo.append(current)
        for nxt in outgoing.get(current, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if len(topo) != len(nodes):
        issues.append("execution structure produces a dependency cycle")
        return None, issues

    node_levels: dict[str, int] = {}
    for node_id in topo:
        deps = node_by_id[node_id].depends_on
        if not deps:
            node_levels[node_id] = 0
        else:
            node_levels[node_id] = max(node_levels.get(dep, 0) + 1 for dep in deps)

    node_payload: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_payload[node.id] = {
            "id": node.id,
            "kind": node.kind,
            "group": node.group,
            "depends_on": list(node.depends_on),
            "task": node.task,
            "agent_profile_id": node.agent_profile_id,
            "role": node.role,
            "phase": node.phase,
            "lane": node.lane,
            "source_task_id": node.source_task_id,
        }

    return (
        RuntimeExecutionPlan(
            nodes=nodes,
            node_payload=node_payload,
            node_levels=node_levels,
            groups=dict(groups),
            parallel_limit=int(plan.constraints.max_parallel),
            task_parse_issues=list(task_parse_issues or []),
        ),
        issues,
    )
