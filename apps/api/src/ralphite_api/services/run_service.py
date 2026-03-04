from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
import json
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ralphite_api.models import PlanFile, Run, RunArtifact, RunEvent, RunNode, RunPermissionSnapshot, ToolPolicy
from ralphite_schemas.plan import EdgeWhen
from ralphite_schemas.validation import compile_plan


def add_run_event(
    db: Session,
    run_id: str,
    *,
    group: str | None,
    task_id: str | None,
    stage: str,
    event: str,
    level: str,
    message: str,
    meta: dict[str, Any] | None = None,
) -> RunEvent:
    record = RunEvent(
        run_id=run_id,
        group_name=group,
        task_id=task_id,
        stage=stage,
        event=event,
        level=level,
        message=message,
        meta=meta or {},
    )
    db.add(record)
    return record


def make_permission_snapshot(db: Session, project_id: str, run_id: str) -> dict:
    policy = db.scalar(select(ToolPolicy).where(ToolPolicy.project_id == project_id))
    snapshot = {
        "allow_tools": policy.allow_tools if policy else ["tool:*"],
        "deny_tools": policy.deny_tools if policy else [],
        "allow_mcps": policy.allow_mcps if policy else ["mcp:*"],
        "deny_mcps": policy.deny_mcps if policy else [],
        "captured_at": datetime.now(UTC).isoformat(),
    }
    db.add(RunPermissionSnapshot(run_id=run_id, snapshot_json=snapshot))
    return snapshot


def create_run_nodes(db: Session, run: Run) -> None:
    plan = run.plan_spec
    from ralphite_schemas.plan import PlanSpecV1

    parsed = PlanSpecV1.model_validate(plan)
    compiled = compile_plan(parsed)
    agents_by_id = {agent.id: agent.model_dump(mode="json") for agent in parsed.agents}

    for node in parsed.graph.nodes:
        db.add(
            RunNode(
                run_id=run.id,
                node_id=node.id,
                group_name=node.group,
                kind=node.kind.value,
                status="queued",
                depends_on=node.depends_on,
                payload={**node.model_dump(mode="json"), "agent": agents_by_id.get(node.agent_id)},
            )
        )

    run.metadata_json = {
        "plan_id": parsed.plan_id,
        "loop_counts": {loop.id: 0 for loop in parsed.graph.loops},
        "compiled": {
            "node_levels": compiled.node_levels,
            "groups": compiled.groups,
            "edges": [edge.model_dump(mode="json", by_alias=True) for edge in parsed.graph.edges],
            "loops": [loop.model_dump(mode="json") for loop in parsed.graph.loops],
            "constraints": parsed.constraints.model_dump(mode="json"),
        },
    }


def is_node_ready(db: Session, node: RunNode) -> bool:
    if node.status != "queued":
        return False
    if not node.depends_on:
        return True

    rows = db.scalars(select(RunNode).where(and_(RunNode.run_id == node.run_id, RunNode.node_id.in_(node.depends_on)))).all()
    status_by_id = {row.node_id: row.status for row in rows}
    return all(status_by_id.get(dep) == "succeeded" for dep in node.depends_on)


def next_ready_node(db: Session, run_id: str) -> RunNode | None:
    queued = db.scalars(
        select(RunNode).where(and_(RunNode.run_id == run_id, RunNode.status == "queued")).order_by(RunNode.created_at.asc())
    ).all()
    for node in queued:
        if is_node_ready(db, node):
            node.status = "running"
            node.attempt_count += 1
            return node
    return None


def reset_subgraph_for_retry(db: Session, run_id: str, start_node: str) -> list[str]:
    nodes = db.scalars(select(RunNode).where(RunNode.run_id == run_id)).all()
    by_node = {node.node_id: node for node in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for dep in node.depends_on:
            adjacency[dep].append(node.node_id)

    touched: list[str] = []
    q = deque([start_node])
    seen: set[str] = set()
    while q:
        current = q.popleft()
        if current in seen:
            continue
        seen.add(current)
        rec = by_node.get(current)
        if rec and rec.status in {"succeeded", "failed", "blocked"}:
            rec.status = "queued"
            rec.result_json = None
            touched.append(rec.node_id)
        for nxt in adjacency.get(current, []):
            q.append(nxt)
    return touched


def maybe_finalize_run(db: Session, run: Run) -> None:
    nodes = db.scalars(select(RunNode).where(RunNode.run_id == run.id)).all()
    statuses = {node.status for node in nodes}

    constraints = (run.metadata_json or {}).get("compiled", {}).get("constraints", {})
    fail_fast = bool(constraints.get("fail_fast", True))

    if "failed" in statuses and fail_fast:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        add_run_event(
            db,
            run.id,
            group=None,
            task_id=None,
            stage="summary",
            event="RUN_DONE",
            level="error",
            message="run completed with failure",
            meta={"status": run.status},
        )
        return

    active = {"queued", "running"}
    if not statuses.intersection(active):
        run.status = "succeeded" if "failed" not in statuses else "failed"
        run.completed_at = datetime.now(UTC)

        summary = {
            "groups": defaultdict(int),
            "tasks": {"succeeded": 0, "failed": 0, "blocked": 0, "total": len(nodes)},
        }
        for node in nodes:
            summary["groups"][node.group_name] += 1
            if node.status == "succeeded":
                summary["tasks"]["succeeded"] += 1
            elif node.status == "failed":
                summary["tasks"]["failed"] += 1
            elif node.status == "blocked":
                summary["tasks"]["blocked"] += 1

        narrative = [
            f"# Run {run.id} Summary",
            "",
            f"Status: **{run.status}**",
            f"Succeeded nodes: {summary['tasks']['succeeded']}",
            f"Failed nodes: {summary['tasks']['failed']}",
            "",
            "## Groups",
        ]
        for group, count in sorted(summary["groups"].items()):
            narrative.append(f"- {group}: {count} node(s)")

        db.add(RunArtifact(run_id=run.id, artifact_id="final_report", format="markdown", content="\n".join(narrative)))
        db.add(
            RunArtifact(
                run_id=run.id,
                artifact_id="machine_bundle",
                format="json",
                content=json.dumps({"run_id": run.id, "status": run.status, "summary": summary}, default=list),
            )
        )

        add_run_event(
            db,
            run.id,
            group=None,
            task_id=None,
            stage="summary",
            event="RUN_SUMMARY",
            level="info",
            message="run summary emitted",
            meta={"status": run.status, "task_summary": summary["tasks"]},
        )
        add_run_event(
            db,
            run.id,
            group=None,
            task_id=None,
            stage="summary",
            event="RUN_DONE",
            level="info" if run.status == "succeeded" else "error",
            message="run completed",
            meta={"status": run.status},
        )


def find_run_plan_content(db: Session, run_input: dict) -> tuple[str, str | None]:
    if run_input.get("plan_content"):
        return run_input["plan_content"], None

    plan_file_id = run_input.get("plan_file_id")
    if not plan_file_id:
        raise ValueError("plan_file_id or plan_content is required")

    plan_file = db.scalar(select(PlanFile).where(PlanFile.id == plan_file_id))
    if not plan_file:
        raise ValueError("plan_file_id not found")

    if plan_file.content:
        return plan_file.content, plan_file.id

    raise ValueError("selected plan file does not include inline content")


def edge_lookup(run: Run) -> list[dict[str, Any]]:
    return (run.metadata_json or {}).get("compiled", {}).get("edges", [])


def loop_max_by_id(run: Run) -> dict[str, int]:
    loops = (run.metadata_json or {}).get("compiled", {}).get("loops", [])
    return {row["id"]: int(row["max_iterations"]) for row in loops}


def apply_gate_decision(db: Session, run: Run, node: RunNode, decision: str) -> dict[str, Any]:
    edges = edge_lookup(run)
    loop_counts = dict((run.metadata_json or {}).get("loop_counts", {}))
    loop_max = loop_max_by_id(run)

    if decision == "pass":
        add_run_event(
            db,
            run.id,
            group=node.group_name,
            task_id=node.node_id,
            stage="orchestrator",
            event="GATE_PASS",
            level="info",
            message="gate passed",
        )
        return {"decision": "pass"}

    if decision == "fail":
        node.status = "failed"
        add_run_event(
            db,
            run.id,
            group=node.group_name,
            task_id=node.node_id,
            stage="orchestrator",
            event="GATE_FAIL",
            level="error",
            message="gate failed",
        )
        return {"decision": "fail"}

    # retry
    retry_edges = [edge for edge in edges if edge["from"] == node.node_id and edge.get("when") == EdgeWhen.RETRY.value]
    touched: list[str] = []
    exhausted = False

    for edge in retry_edges:
        loop_id = edge.get("loop_id")
        target = edge["to"]
        if loop_id:
            count = int(loop_counts.get(loop_id, 0))
            max_count = int(loop_max.get(loop_id, 1))
            if count >= max_count:
                exhausted = True
                continue
            loop_counts[loop_id] = count + 1
        touched.extend(reset_subgraph_for_retry(db, run.id, target))

    run.metadata_json = {**(run.metadata_json or {}), "loop_counts": loop_counts}

    if exhausted and not touched:
        node.status = "failed"
        add_run_event(
            db,
            run.id,
            group=node.group_name,
            task_id=node.node_id,
            stage="orchestrator",
            event="GATE_FAIL",
            level="error",
            message="gate retry exhausted",
        )
        return {"decision": "retry_exhausted"}

    add_run_event(
        db,
        run.id,
        group=node.group_name,
        task_id=node.node_id,
        stage="orchestrator",
        event="GATE_RETRY",
        level="warn",
        message="gate requested retry",
        meta={"touched_nodes": touched, "loop_counts": loop_counts},
    )
    return {"decision": "retry", "touched_nodes": touched, "loop_counts": loop_counts}
