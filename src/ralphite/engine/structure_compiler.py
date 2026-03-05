from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from ralphite.schemas.plan import BehaviorKind, OrchestrationTemplate, PlanSpec

from .task_parser import ParsedTask, task_acceptance_payload


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
    cell_id: str
    team: str | None = None
    behavior_id: str | None = None
    behavior_kind: str | None = None
    behavior_prompt_template: str | None = None
    source_task_id: str | None = None
    block_index: int = 0
    acceptance: dict[str, Any] | None = None


@dataclass(slots=True)
class RuntimeBlockSpec:
    index: int
    kind: str
    cell_id: str
    lane: str
    team: str | None
    behavior_id: str | None
    node_ids: list[str]
    task_ids: list[str]


@dataclass(slots=True)
class RuntimeCellSpec:
    id: str
    kind: str
    lane: str
    team: str | None
    behavior_id: str | None
    task_ids: list[str]
    node_ids: list[str]
    depends_on_cells: list[str]
    template: str


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
    resolved_cells: list[RuntimeCellSpec]
    task_assignment: dict[str, str]
    compile_warnings: list[str]


@dataclass(slots=True)
class _BehaviorChoice:
    behavior_id: str | None
    behavior_kind: str
    agent_id: str
    prompt_template: str | None


def compile_execution_structure(
    plan: PlanSpec,
    tasks: list[ParsedTask],
    *,
    task_parse_issues: list[str] | None = None,
) -> tuple[RuntimeExecutionPlan | None, list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    profile_ids = {profile.id for profile in plan.agents}
    worker_ids = [
        profile.id for profile in plan.agents if profile.role.value == "worker"
    ]
    orchestrator_ids = [
        profile.id for profile in plan.agents if profile.role.value == "orchestrator"
    ]
    if not worker_ids:
        issues.append("no worker agent profiles available")
    if not orchestrator_ids:
        issues.append("no orchestrator agent profiles available")

    behavior_by_id = {
        behavior.id: behavior
        for behavior in plan.orchestration.behaviors
        if behavior.enabled
    }
    first_worker = (
        "worker_default"
        if "worker_default" in profile_ids
        else (worker_ids[0] if worker_ids else "")
    )
    first_orchestrator = (
        "orchestrator_default"
        if "orchestrator_default" in profile_ids
        else (orchestrator_ids[0] if orchestrator_ids else "")
    )

    pending_tasks = [task for task in tasks if not task.completed]
    pending_tasks.sort(key=lambda row: row.order)
    tasks_by_id = {task.id: task for task in pending_tasks}
    if len(tasks_by_id) != len(pending_tasks):
        issues.append("duplicate pending task ids detected")

    def _choose_behavior(
        *,
        explicit_behavior_id: str | None = None,
        fallback_kind: BehaviorKind = BehaviorKind.CUSTOM,
        cell_id: str,
    ) -> _BehaviorChoice:
        if explicit_behavior_id:
            behavior = behavior_by_id.get(explicit_behavior_id)
            if not behavior:
                issues.append(
                    f"cell '{cell_id}' references unknown behavior '{explicit_behavior_id}'"
                )
            else:
                agent_id = behavior.agent or first_orchestrator
                if agent_id not in profile_ids:
                    issues.append(
                        f"cell '{cell_id}' behavior '{explicit_behavior_id}' resolved unknown agent '{agent_id}'"
                    )
                return _BehaviorChoice(
                    behavior_id=behavior.id,
                    behavior_kind=behavior.kind.value,
                    agent_id=agent_id,
                    prompt_template=behavior.prompt_template,
                )

        for behavior in behavior_by_id.values():
            if behavior.kind == fallback_kind:
                agent_id = behavior.agent or first_orchestrator
                if agent_id not in profile_ids:
                    issues.append(
                        f"cell '{cell_id}' behavior '{behavior.id}' resolved unknown agent '{agent_id}'"
                    )
                return _BehaviorChoice(
                    behavior_id=behavior.id,
                    behavior_kind=behavior.kind.value,
                    agent_id=agent_id,
                    prompt_template=behavior.prompt_template,
                )

        warnings.append(
            f"no enabled behavior found for '{fallback_kind.value}' in cell '{cell_id}'; using defaults"
        )
        return _BehaviorChoice(
            behavior_id=None,
            behavior_kind=fallback_kind.value,
            agent_id=first_orchestrator,
            prompt_template=None,
        )

    nodes: list[RuntimeNodeSpec] = []
    groups: dict[str, list[str]] = defaultdict(list)
    blocks: list[RuntimeBlockSpec] = []
    resolved_cells: list[RuntimeCellSpec] = []
    node_block_index: dict[str, int] = {}
    task_assignment: dict[str, str] = {}

    anchor: list[str] = []
    phase_id = "phase-1"
    block_index = 0
    cell_outputs: dict[str, list[str]] = {}
    task_to_latest_node_id: dict[str, str] = {}

    def add_node(node: RuntimeNodeSpec) -> None:
        nodes.append(node)
        groups[node.group].append(node.id)
        node_block_index[node.id] = node.block_index
        if node.source_task_id:
            task_to_latest_node_id[node.source_task_id] = node.id

    def add_block(
        *,
        kind: str,
        cell_id: str,
        lane: str,
        team: str | None,
        behavior_id: str | None,
        node_ids: list[str],
        task_ids: list[str],
    ) -> None:
        blocks.append(
            RuntimeBlockSpec(
                index=block_index,
                kind=kind,
                cell_id=cell_id,
                lane=lane,
                team=team,
                behavior_id=behavior_id,
                node_ids=list(node_ids),
                task_ids=list(task_ids),
            )
        )
        resolved_cells.append(
            RuntimeCellSpec(
                id=cell_id,
                kind=kind,
                lane=lane,
                team=team,
                behavior_id=behavior_id,
                task_ids=list(task_ids),
                node_ids=list(node_ids),
                depends_on_cells=[],
                template=plan.orchestration.template.value,
            )
        )
        cell_outputs[cell_id] = list(node_ids)

    def append_worker_segment(
        *,
        cell_id: str,
        segment_kind: str,
        segment_tasks: list[ParsedTask],
        lane: str,
        team: str | None = None,
        variant: str | None = None,
        base_anchor: list[str] | None = None,
    ) -> list[str]:
        nonlocal block_index, anchor
        if not segment_tasks:
            return list(base_anchor or anchor)

        active_anchor = list(base_anchor if base_anchor is not None else anchor)
        node_ids: list[str] = []
        task_ids: list[str] = []
        local_anchor = list(active_anchor)

        for task in segment_tasks:
            agent_id = task.agent or first_worker
            if agent_id not in profile_ids:
                issues.append(f"task '{task.id}' references unknown agent '{agent_id}'")
            suffix = f"::{variant}" if variant else ""
            node_id = f"{phase_id}::task::{task.id}{suffix}"
            depends_on = list(
                active_anchor if segment_kind == "parallel" else local_anchor
            )
            node = RuntimeNodeSpec(
                id=node_id,
                kind="agent",
                group=phase_id,
                depends_on=depends_on,
                task=task.description or task.title,
                agent_profile_id=agent_id,
                role="worker",
                phase=phase_id,
                lane=lane,
                cell_id=cell_id,
                team=team,
                source_task_id=task.id,
                block_index=block_index,
                acceptance=task_acceptance_payload(task),
            )
            add_node(node)
            node_ids.append(node_id)
            task_ids.append(task.id)
            task_assignment[task.id] = cell_id
            if segment_kind == "sequential":
                local_anchor = [node_id]

        next_anchor = list(node_ids if segment_kind == "parallel" else local_anchor)
        add_block(
            kind=segment_kind,
            cell_id=cell_id,
            lane=lane,
            team=team,
            behavior_id=None,
            node_ids=node_ids,
            task_ids=task_ids,
        )
        block_index += 1
        if base_anchor is None:
            anchor = list(next_anchor)
        return next_anchor

    def append_orchestrator_cell(
        *,
        cell_id: str,
        behavior_id: str | None = None,
        fallback_kind: BehaviorKind = BehaviorKind.CUSTOM,
        base_anchor: list[str] | None = None,
    ) -> list[str]:
        nonlocal block_index, anchor
        choice = _choose_behavior(
            explicit_behavior_id=behavior_id,
            fallback_kind=fallback_kind,
            cell_id=cell_id,
        )
        if choice.agent_id not in profile_ids:
            issues.append(
                f"cell '{cell_id}' references unknown orchestrator agent '{choice.agent_id}'"
            )
        if not choice.agent_id:
            issues.append(f"cell '{cell_id}' has no orchestrator agent available")

        active_anchor = list(base_anchor if base_anchor is not None else anchor)
        message = {
            BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION.value: "Merge outputs and resolve conflicts before downstream dispatch.",
            BehaviorKind.SUMMARIZE_WORK.value: "Summarize completed work and prepare downstream handoff notes.",
            BehaviorKind.PREPARE_DISPATCH.value: "Prepare lane/worktree dispatch for upcoming worker cells.",
            BehaviorKind.CUSTOM.value: "Execute custom orchestration behavior.",
        }.get(choice.behavior_kind, "Execute orchestration behavior.")
        node_id = f"{phase_id}::orchestrator::{cell_id}"
        node = RuntimeNodeSpec(
            id=node_id,
            kind="agent",
            group=phase_id,
            depends_on=active_anchor,
            task=message,
            agent_profile_id=choice.agent_id,
            role="orchestrator",
            phase=phase_id,
            lane="orchestrator",
            cell_id=cell_id,
            behavior_id=choice.behavior_id,
            behavior_kind=choice.behavior_kind,
            behavior_prompt_template=choice.prompt_template,
            block_index=block_index,
        )
        add_node(node)
        add_block(
            kind="orchestrator",
            cell_id=cell_id,
            lane="orchestrator",
            team=None,
            behavior_id=choice.behavior_id,
            node_ids=[node_id],
            task_ids=[],
        )
        block_index += 1
        if base_anchor is None:
            anchor = [node_id]
        return [node_id]

    if plan.orchestration.template == OrchestrationTemplate.GENERAL_SPS:
        explicit_cell_map = {
            task.id: (task.routing_cell or "").strip() for task in pending_tasks
        }

        seq_pre: list[ParsedTask] = []
        par_core: list[ParsedTask] = []
        seq_post: list[ParsedTask] = []
        for task in pending_tasks:
            explicit = explicit_cell_map.get(task.id)
            if explicit:
                if explicit in {"seq_pre", "seq_prelude"}:
                    seq_pre.append(task)
                elif explicit in {"par_core", "parallel_core"}:
                    par_core.append(task)
                elif explicit in {"seq_post", "seq_postlude"}:
                    seq_post.append(task)
                else:
                    warnings.append(
                        f"task '{task.id}' has unknown routing.cell '{explicit}', defaulting to seq_pre"
                    )
                    seq_pre.append(task)
            else:
                seq_pre.append(task)

        if seq_pre:
            append_worker_segment(
                cell_id="seq_pre",
                segment_kind="sequential",
                segment_tasks=seq_pre,
                lane="sequential",
            )
        append_orchestrator_cell(
            cell_id="orch_merge_1",
            fallback_kind=BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION,
        )
        if not par_core:
            warnings.append(
                "general_sps has no tasks mapped to par_core; continuing with orchestrator merge cells only"
            )
        for seg_idx, seg_tasks in enumerate([par_core] if par_core else [], start=1):
            append_worker_segment(
                cell_id=f"par_core_{seg_idx}",
                segment_kind="parallel",
                segment_tasks=seg_tasks,
                lane="parallel",
            )
        append_orchestrator_cell(
            cell_id="orch_merge_2",
            fallback_kind=BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION,
        )
        if seq_post:
            append_worker_segment(
                cell_id="seq_post",
                segment_kind="sequential",
                segment_tasks=seq_post,
                lane="sequential",
            )
        append_orchestrator_cell(
            cell_id="orch_finalize",
            fallback_kind=BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION,
        )

    elif plan.orchestration.template == OrchestrationTemplate.BRANCHED:
        lanes = list(plan.orchestration.branched.lanes)
        lane_tasks: dict[str, list[ParsedTask]] = {lane: [] for lane in lanes}
        trunk_pre: list[ParsedTask] = []
        trunk_post: list[ParsedTask] = []

        for task in pending_tasks:
            lane = (task.routing_lane or "").strip()
            cell = (task.routing_cell or "").strip()
            if lane and lane in lane_tasks:
                lane_tasks[lane].append(task)
                continue
            if cell == "trunk_post":
                trunk_post.append(task)
            else:
                trunk_pre.append(task)

        if trunk_pre:
            append_worker_segment(
                cell_id="trunk_pre",
                segment_kind="sequential",
                segment_tasks=trunk_pre,
                lane="trunk",
            )
        split_anchor = append_orchestrator_cell(
            cell_id="split_dispatch", fallback_kind=BehaviorKind.PREPARE_DISPATCH
        )
        lane_merge_nodes: list[str] = []
        for lane in lanes:
            lane_anchor = list(split_anchor)
            lane_rows = sorted(lane_tasks.get(lane, []), key=lambda row: row.order)
            if lane_rows:
                lane_anchor = append_worker_segment(
                    cell_id=f"{lane}_seg_1",
                    segment_kind="sequential",
                    segment_tasks=lane_rows,
                    lane=lane,
                    base_anchor=lane_anchor,
                )
            lane_merge = append_orchestrator_cell(
                cell_id=f"{lane}_merge",
                fallback_kind=BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION,
                base_anchor=lane_anchor,
            )
            lane_merge_nodes.extend(lane_merge)

        if not lane_merge_nodes:
            lane_merge_nodes = list(split_anchor)
        join_anchor = append_orchestrator_cell(
            cell_id="join_lanes",
            fallback_kind=BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION,
            base_anchor=lane_merge_nodes,
        )
        anchor = list(join_anchor)
        if trunk_post:
            append_worker_segment(
                cell_id="trunk_post",
                segment_kind="sequential",
                segment_tasks=trunk_post,
                lane="trunk",
            )
        append_orchestrator_cell(
            cell_id="branched_finalize", fallback_kind=BehaviorKind.SUMMARIZE_WORK
        )

    elif plan.orchestration.template == OrchestrationTemplate.BLUE_RED:
        for task in pending_tasks:
            append_orchestrator_cell(
                cell_id=f"{task.id}_prepare",
                fallback_kind=BehaviorKind.PREPARE_DISPATCH,
            )
            append_worker_segment(
                cell_id=f"{task.id}_blue",
                segment_kind="sequential",
                segment_tasks=[task],
                lane="blue",
                team="blue",
                variant="blue",
            )
            append_orchestrator_cell(
                cell_id=f"{task.id}_handoff",
                fallback_kind=BehaviorKind.SUMMARIZE_WORK,
            )
            append_worker_segment(
                cell_id=f"{task.id}_red",
                segment_kind="sequential",
                segment_tasks=[task],
                lane="red",
                team="red",
                variant="red",
            )
            append_orchestrator_cell(
                cell_id=f"{task.id}_merge",
                fallback_kind=BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION,
            )
        append_orchestrator_cell(
            cell_id="blue_red_finalize", fallback_kind=BehaviorKind.SUMMARIZE_WORK
        )

    else:
        custom_cells = list(plan.orchestration.custom.cells)
        for cell in custom_cells:
            base_anchor: list[str] | None = None
            if cell.depends_on:
                resolved: list[str] = []
                for dep_id in cell.depends_on:
                    if dep_id in cell_outputs:
                        resolved.extend(cell_outputs[dep_id])
                    else:
                        issues.append(
                            f"custom cell '{cell.id}' depends on unknown cell '{dep_id}'"
                        )
                base_anchor = resolved
            selected_tasks = []
            if cell.task_ids:
                for task_id in cell.task_ids:
                    row = tasks_by_id.get(task_id)
                    if row:
                        selected_tasks.append(row)
                    else:
                        issues.append(
                            f"custom cell '{cell.id}' references unknown task '{task_id}'"
                        )
            else:
                selected_tasks = [
                    task
                    for task in pending_tasks
                    if (task.routing_cell or "").strip() == cell.id
                ]
            selected_tasks.sort(key=lambda row: row.order)

            if cell.kind.value in {"sequential", "parallel"}:
                append_worker_segment(
                    cell_id=cell.id,
                    segment_kind=cell.kind.value,
                    segment_tasks=selected_tasks,
                    lane=cell.lane
                    or ("parallel" if cell.kind.value == "parallel" else "sequential"),
                    team=cell.team,
                    base_anchor=base_anchor,
                )
                continue
            if cell.kind.value == "team_cycle":
                for task in selected_tasks:
                    local = append_worker_segment(
                        cell_id=f"{cell.id}_{task.id}_blue",
                        segment_kind="sequential",
                        segment_tasks=[task],
                        lane=cell.lane or "blue",
                        team="blue",
                        variant="blue",
                        base_anchor=base_anchor,
                    )
                    base_anchor = append_worker_segment(
                        cell_id=f"{cell.id}_{task.id}_red",
                        segment_kind="sequential",
                        segment_tasks=[task],
                        lane=cell.lane or "red",
                        team="red",
                        variant="red",
                        base_anchor=local,
                    )
                continue
            append_orchestrator_cell(
                cell_id=cell.id,
                behavior_id=cell.behavior,
                fallback_kind=BehaviorKind.PREPARE_DISPATCH,
                base_anchor=base_anchor,
            )

    node_by_id = {node.id: node for node in nodes}
    node_position = {node.id: idx for idx, node in enumerate(nodes)}
    for node in nodes:
        if node.role != "worker" or not node.source_task_id:
            continue
        task = tasks_by_id.get(node.source_task_id)
        if not task:
            continue
        for dep_task_id in task.depends_on:
            dep_node_id = task_to_latest_node_id.get(dep_task_id)
            if not dep_node_id:
                issues.append(
                    f"task '{task.id}' depends_on '{dep_task_id}' but dependency is not selected for execution"
                )
                continue
            if dep_node_id == node.id:
                issues.append(f"task '{task.id}' has self dependency")
                continue
            dep_node = node_by_id.get(dep_node_id)
            if (
                dep_node
                and dep_node.block_index > node.block_index
                and dep_node.id != node.id
            ):
                issues.append(
                    f"task '{task.id}' depends on '{dep_task_id}' from a later block; forward dependencies are not allowed"
                )
                continue
            if (
                dep_node
                and dep_node.block_index == node.block_index
                and node_position.get(dep_node_id, -1) >= node_position.get(node.id, -1)
            ):
                issues.append(
                    f"task '{task.id}' depends on '{dep_task_id}' from the same block but non-prior order"
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
            "cell_id": node.cell_id,
            "team": node.team,
            "behavior_id": node.behavior_id,
            "behavior_kind": node.behavior_kind,
            "behavior_prompt_template": node.behavior_prompt_template,
            "source_task_id": node.source_task_id,
            "block_index": node.block_index,
            "acceptance": node.acceptance or {},
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
            resolved_cells=resolved_cells,
            task_assignment=task_assignment,
            compile_warnings=warnings,
        ),
        issues,
    )
