from __future__ import annotations

from decimal import Decimal
from typing import Any

from ralphite_engine.models import AgentRowState, EdgeRowState, StepRowState
from ralphite_schemas.plan import PlanSpecV1


def plan_to_rows(plan: PlanSpecV1) -> dict[str, Any]:
    steps = [
        StepRowState(
            id=node.id,
            kind=node.kind.value,
            group=node.group,
            depends_on=list(node.depends_on),
            agent_id=node.agent_id,
            task=node.task,
            gate_mode=node.gate.mode if node.gate else None,
            gate_pass_if=node.gate.pass_if if node.gate else None,
        )
        for node in plan.graph.nodes
    ]
    edges = [
        EdgeRowState(from_node=edge.from_node, to=edge.to, when=edge.when.value, loop_id=edge.loop_id)
        for edge in plan.graph.edges
    ]
    agents = [
        AgentRowState(
            id=agent.id,
            provider=agent.provider,
            model=agent.model,
            system_prompt=agent.system_prompt,
            tools_allow=list(agent.tools_allow),
        )
        for agent in plan.agents
    ]
    loops = [{"id": loop.id, "max_iterations": int(loop.max_iterations)} for loop in plan.graph.loops]
    constraints = {
        "max_runtime_seconds": int(plan.constraints.max_runtime_seconds),
        "max_total_steps": int(plan.constraints.max_total_steps),
        "max_cost_usd": str(plan.constraints.max_cost_usd),
        "fail_fast": bool(plan.constraints.fail_fast),
    }
    outputs = {
        "required_artifacts": [
            {"id": artifact.id, "format": artifact.format}
            for artifact in plan.outputs.required_artifacts
        ]
    }
    return {
        "version": int(plan.version),
        "plan_id": plan.plan_id,
        "name": plan.name,
        "workspace": plan.workspace.model_dump(mode="json"),
        "materials": plan.materials.model_dump(mode="json"),
        "steps": steps,
        "edges": edges,
        "loops": loops,
        "agents": agents,
        "constraints": constraints,
        "outputs": outputs,
    }


def rows_to_plan_data(buffer: dict[str, Any]) -> dict[str, Any]:
    raw_steps: list[Any] = buffer.get("steps", [])
    raw_edges: list[Any] = buffer.get("edges", [])
    raw_agents: list[Any] = buffer.get("agents", [])

    steps: list[StepRowState] = [
        row if isinstance(row, StepRowState) else StepRowState.model_validate(row)
        for row in raw_steps
    ]
    edges: list[EdgeRowState] = [
        row if isinstance(row, EdgeRowState) else EdgeRowState.model_validate(row)
        for row in raw_edges
    ]
    agents: list[AgentRowState] = [
        row if isinstance(row, AgentRowState) else AgentRowState.model_validate(row)
        for row in raw_agents
    ]

    nodes: list[dict[str, Any]] = []
    for step in steps:
        node: dict[str, Any] = {
            "id": step.id,
            "kind": step.kind,
            "group": step.group,
            "depends_on": list(step.depends_on),
        }
        if step.kind == "agent":
            node["agent_id"] = step.agent_id
            node["task"] = step.task or ""
        elif step.kind == "gate":
            node["gate"] = {
                "mode": step.gate_mode or "rubric",
                "pass_if": step.gate_pass_if or "all_acceptance_checks_pass",
            }
        nodes.append(node)

    edge_rows = [
        {
            "from": edge.from_node,
            "to": edge.to,
            "when": edge.when,
            **({"loop_id": edge.loop_id} if edge.loop_id else {}),
        }
        for edge in edges
    ]

    loop_rows = []
    for loop in buffer.get("loops", []):
        loop_rows.append(
            {
                "id": str(loop.get("id", "main_loop")),
                "max_iterations": int(loop.get("max_iterations", 1)),
            }
        )

    agent_rows = []
    for agent in agents:
        agent_rows.append(
            {
                "id": agent.id,
                "provider": agent.provider,
                "model": agent.model,
                "system_prompt": agent.system_prompt,
                "tools_allow": list(agent.tools_allow),
            }
        )

    constraints = buffer.get("constraints", {})
    outputs = buffer.get("outputs", {})

    return {
        "version": int(buffer.get("version", 1)),
        "plan_id": str(buffer.get("plan_id", "edited-plan")),
        "name": str(buffer.get("name", "Edited Plan")),
        "workspace": buffer.get("workspace", {}),
        "materials": buffer.get("materials", {}),
        "agents": agent_rows,
        "graph": {
            "nodes": nodes,
            "edges": edge_rows,
            "loops": loop_rows,
        },
        "constraints": {
            "max_runtime_seconds": int(constraints.get("max_runtime_seconds", 3600)),
            "max_total_steps": int(constraints.get("max_total_steps", 120)),
            "max_cost_usd": str(Decimal(str(constraints.get("max_cost_usd", "10.0")))),
            "fail_fast": bool(constraints.get("fail_fast", True)),
        },
        "outputs": {
            "required_artifacts": [
                {
                    "id": str(item.get("id", "artifact")),
                    "format": str(item.get("format", "markdown")),
                }
                for item in outputs.get("required_artifacts", [])
            ]
        },
    }


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
