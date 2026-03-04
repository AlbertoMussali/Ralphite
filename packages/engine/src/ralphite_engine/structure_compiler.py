from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from ralphite_schemas.plan_v4 import PlanSpecV4

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
    parallel_group: int | None = None
    block_index: int = 0


@dataclass(slots=True)
class RuntimeBlockSpec:
    index: int
    kind: str  # sequential | parallel
    parallel_group: int
    node_ids: list[str]
    task_ids: list[str]


@dataclass(slots=True)
class RuntimeExecutionPlan:
    nodes: list[RuntimeNodeSpec]
    node_payload: dict[str, dict[str, Any]]
    node_levels: dict[str, int]
    groups: dict[str, list[str]]
    parallel_limit: int
    task_parse_issues: list[str]
    blocks: list[RuntimeBlockSpec]
    node_block_index: dict[str, int]


def compile_execution_structure(
    plan: PlanSpecV4,
    tasks: list[ParsedTask],
    *,
    task_parse_issues: list[str] | None = None,
) -> tuple[RuntimeExecutionPlan | None, list[str]]:
    issues: list[str] = []
    profile_ids = {profile.id for profile in plan.agents}

    pending_tasks = [task for task in tasks if not task.completed]
    pending_tasks.sort(key=lambda row: row.order)
    tasks_by_id = {task.id: task for task in pending_tasks}
    if len(tasks_by_id) != len(pending_tasks):
        issues.append("duplicate pending task ids detected")

    nodes: list[RuntimeNodeSpec] = []
    groups: dict[str, list[str]] = defaultdict(list)
    task_to_node_id: dict[str, str] = {}
    blocks: list[RuntimeBlockSpec] = []
    node_block_index: dict[str, int] = {}

    def add_node(node: RuntimeNodeSpec) -> None:
        nodes.append(node)
        groups[node.group].append(node.id)
        node_block_index[node.id] = node.block_index

    anchor: list[str] = []
    block_index = 0
    phase_id = "phase-1"

    if plan.run.pre_orchestrator.enabled:
        pre_agent = plan.run.pre_orchestrator.agent
        if pre_agent not in profile_ids:
            issues.append(f"unknown pre orchestrator agent '{pre_agent}'")
        pre_node = RuntimeNodeSpec(
            id=f"{phase_id}::orchestrator_pre",
            kind="agent",
            group=phase_id,
            depends_on=list(anchor),
            task="Prepare run execution order and workspace state before workers run.",
            agent_profile_id=pre_agent,
            role="orchestrator_pre",
            phase=phase_id,
            lane="orchestrator_pre",
            block_index=block_index,
        )
        add_node(pre_node)
        anchor = [pre_node.id]
        block_index += 1

    used_parallel_groups: set[int] = set()
    last_group = 0
    idx = 0
    while idx < len(pending_tasks):
        current = pending_tasks[idx]
        group = int(current.parallel_group or 0)

        if group > 0:
            if group < last_group:
                issues.append(f"parallel_group {group} appears after {last_group}; groups must be non-decreasing")
            if group in used_parallel_groups:
                issues.append(f"parallel_group {group} appears in non-contiguous blocks")
            used_parallel_groups.add(group)
            last_group = group

            group_tasks: list[ParsedTask] = []
            start_idx = idx
            while idx < len(pending_tasks) and int(pending_tasks[idx].parallel_group or 0) == group:
                group_tasks.append(pending_tasks[idx])
                idx += 1

            group_anchor = list(anchor)
            block_node_ids: list[str] = []
            block_task_ids: list[str] = []
            for task in group_tasks:
                agent_id = task.agent or "worker_default"
                if agent_id not in profile_ids:
                    issues.append(f"task '{task.id}' references unknown agent '{agent_id}'")
                node_id = f"{phase_id}::task::{task.id}"
                task_to_node_id[task.id] = node_id
                node = RuntimeNodeSpec(
                    id=node_id,
                    kind="agent",
                    group=phase_id,
                    depends_on=list(group_anchor),
                    task=task.description or task.title,
                    agent_profile_id=agent_id,
                    role="worker",
                    phase=phase_id,
                    lane="parallel",
                    source_task_id=task.id,
                    parallel_group=group,
                    block_index=block_index,
                )
                add_node(node)
                block_node_ids.append(node_id)
                block_task_ids.append(task.id)

            blocks.append(
                RuntimeBlockSpec(
                    index=block_index,
                    kind="parallel",
                    parallel_group=group,
                    node_ids=block_node_ids,
                    task_ids=block_task_ids,
                )
            )
            anchor = list(block_node_ids)
            block_index += 1
            continue

        if last_group > 0:
            used_parallel_groups.add(last_group)

        seq_tasks: list[ParsedTask] = []
        while idx < len(pending_tasks) and int(pending_tasks[idx].parallel_group or 0) <= 0:
            seq_tasks.append(pending_tasks[idx])
            idx += 1

        block_node_ids: list[str] = []
        block_task_ids: list[str] = []
        local_anchor = list(anchor)
        for task in seq_tasks:
            agent_id = task.agent or "worker_default"
            if agent_id not in profile_ids:
                issues.append(f"task '{task.id}' references unknown agent '{agent_id}'")
            node_id = f"{phase_id}::task::{task.id}"
            task_to_node_id[task.id] = node_id
            node = RuntimeNodeSpec(
                id=node_id,
                kind="agent",
                group=phase_id,
                depends_on=list(local_anchor),
                task=task.description or task.title,
                agent_profile_id=agent_id,
                role="worker",
                phase=phase_id,
                lane="sequential",
                source_task_id=task.id,
                parallel_group=None,
                block_index=block_index,
            )
            add_node(node)
            block_node_ids.append(node_id)
            block_task_ids.append(task.id)
            local_anchor = [node_id]

        blocks.append(
            RuntimeBlockSpec(
                index=block_index,
                kind="sequential",
                parallel_group=0,
                node_ids=block_node_ids,
                task_ids=block_task_ids,
            )
        )
        anchor = [block_node_ids[-1]] if block_node_ids else list(anchor)
        block_index += 1

    if plan.run.post_orchestrator.enabled:
        post_agent = plan.run.post_orchestrator.agent
        if post_agent not in profile_ids:
            issues.append(f"unknown post orchestrator agent '{post_agent}'")
        post_node = RuntimeNodeSpec(
            id=f"{phase_id}::orchestrator_post",
            kind="agent",
            group=phase_id,
            depends_on=list(anchor),
            task=(
                "Integrate worker outputs to main while preserving worker commits, clean temporary artifacts/worktrees, "
                "and summarize results for the user."
            ),
            agent_profile_id=post_agent,
            role="orchestrator_post",
            phase=phase_id,
            lane="orchestrator_post",
            block_index=block_index,
        )
        add_node(post_node)

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
                issues.append(f"task '{task.id}' depends_on '{dep_task_id}' but dependency is not selected for execution")
                continue
            if dep_node_id == node.id:
                issues.append(f"task '{task.id}' has self dependency")
                continue
            dep_node = node_by_id.get(dep_node_id)
            if dep_node and dep_node.block_index >= node.block_index and dep_node.id != node.id:
                issues.append(
                    f"task '{task.id}' depends on '{dep_task_id}' from same or later block; forward dependencies are not allowed"
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
            "parallel_group": node.parallel_group,
            "block_index": node.block_index,
        }

    return (
        RuntimeExecutionPlan(
            nodes=nodes,
            node_payload=node_payload,
            node_levels=node_levels,
            groups=dict(groups),
            parallel_limit=int(plan.constraints.max_parallel),
            task_parse_issues=list(task_parse_issues or []),
            blocks=blocks,
            node_block_index=node_block_index,
        ),
        issues,
    )
