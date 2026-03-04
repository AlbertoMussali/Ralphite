from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ralphite_api.api.deps import get_runner
from ralphite_api.db.session import get_db
from ralphite_api.models import (
    Run,
    RunNode,
    RunPermissionSnapshot,
    Runner,
    RunnerCapability,
    WorkspaceConnection,
)
from ralphite_api.schemas.runner import (
    ClaimedNodeResponse,
    RunnerCapabilitiesV1,
    RunnerClaimRequest,
    RunnerEventsBatchRequest,
    RunnerNodeCompleteRequest,
    RunnerNodeFailRequest,
    RunnerRegisterResponse,
)
from ralphite_api.services.auth import make_runner_token
from ralphite_api.services.run_service import (
    add_run_event,
    apply_gate_decision,
    maybe_finalize_run,
    next_ready_node,
)
from ralphite_api.services.plan_sync import sync_plan_files

router = APIRouter(prefix="/runner", tags=["runner"])


@router.post("/register", response_model=RunnerRegisterResponse)
def register_runner(payload: RunnerCapabilitiesV1, db: Session = Depends(get_db)) -> RunnerRegisterResponse:
    runner = db.scalar(select(Runner).where(Runner.id == payload.runner_id))
    if not runner:
        runner = Runner(
            id=payload.runner_id,
            token=make_runner_token(),
            workspace_root=payload.workspace_root,
            runner_version=payload.runner_version,
            status="active",
            last_heartbeat_at=datetime.now(UTC),
        )
        db.add(runner)
    else:
        runner.workspace_root = payload.workspace_root
        runner.runner_version = payload.runner_version
        runner.status = "active"
        runner.last_heartbeat_at = datetime.now(UTC)

    db.add(RunnerCapability(runner_id=payload.runner_id, payload=payload.model_dump(mode="json")))

    workspaces = db.scalars(
        select(WorkspaceConnection).where(WorkspaceConnection.workspace_root == payload.workspace_root)
    ).all()
    for workspace in workspaces:
        workspace.connected_runner_id = payload.runner_id
        workspace.runner_id = payload.runner_id
        workspace.status = "connected"
        workspace.bootstrap_state = "seeded" if payload.seeded_starter else workspace.bootstrap_state
        workspace.updated_at = datetime.now(UTC)
        sync_plan_files(db, workspace.project_id, [row.model_dump(mode="json") for row in payload.plan_files])

    db.commit()
    return RunnerRegisterResponse(runner_id=runner.id, token=runner.token)


@router.post("/heartbeat")
def heartbeat(
    payload: RunnerCapabilitiesV1,
    db: Session = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> dict:
    if payload.runner_id != runner.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="runner id mismatch")

    runner.workspace_root = payload.workspace_root
    runner.runner_version = payload.runner_version
    runner.last_heartbeat_at = datetime.now(UTC)
    runner.status = "active"
    db.add(RunnerCapability(runner_id=runner.id, payload=payload.model_dump(mode="json")))

    workspaces = db.scalars(select(WorkspaceConnection).where(WorkspaceConnection.workspace_root == payload.workspace_root)).all()
    for workspace in workspaces:
        workspace.connected_runner_id = runner.id
        workspace.runner_id = runner.id
        workspace.status = "connected"
        workspace.bootstrap_state = "seeded" if payload.seeded_starter else workspace.bootstrap_state
        workspace.updated_at = datetime.now(UTC)
        sync_plan_files(db, workspace.project_id, [row.model_dump(mode="json") for row in payload.plan_files])

    db.commit()
    return {"ok": True}


@router.post("/claim-next", response_model=ClaimedNodeResponse | None)
def claim_next(
    payload: RunnerClaimRequest,
    db: Session = Depends(get_db),
    runner: Runner = Depends(get_runner),
):
    if payload.runner_id != runner.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="runner id mismatch")

    workspace_projects = db.scalars(
        select(WorkspaceConnection.project_id).where(WorkspaceConnection.connected_runner_id == runner.id)
    ).all()
    if not workspace_projects:
        return None

    runs = db.scalars(
        select(Run)
        .where(and_(Run.project_id.in_(workspace_projects), Run.status == "running"))
        .order_by(Run.created_at.asc())
    ).all()

    for run in runs:
        node = next_ready_node(db, run.id)
        if not node:
            continue

        snapshot = db.scalar(select(RunPermissionSnapshot).where(RunPermissionSnapshot.run_id == run.id))
        add_run_event(
            db,
            run.id,
            group=node.group_name,
            task_id=node.node_id,
            stage="task",
            event="NODE_STARTED",
            level="info",
            message="node started",
            meta={"attempt": node.attempt_count},
        )
        db.commit()

        return ClaimedNodeResponse(
            run_id=run.id,
            node_record_id=node.id,
            node_id=node.node_id,
            kind=node.kind,
            group=node.group_name,
            attempt_count=node.attempt_count,
            payload=node.payload,
            permission_snapshot=snapshot.snapshot_json if snapshot else {},
        )

    return None


@router.post("/runs/{run_id}/events/batch")
def ingest_events_batch(
    run_id: str,
    payload: RunnerEventsBatchRequest,
    db: Session = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> dict:
    if payload.runner_id != runner.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="runner id mismatch")

    run = db.scalar(select(Run).where(Run.id == run_id))
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    for event in payload.events:
        add_run_event(
            db,
            run_id,
            group=event.group,
            task_id=event.task_id,
            stage=event.stage,
            event=event.event,
            level=event.level,
            message=event.message,
            meta=event.meta,
        )

    db.commit()
    return {"ok": True, "count": len(payload.events)}


@router.post("/runs/{run_id}/complete")
def complete_node(
    run_id: str,
    payload: RunnerNodeCompleteRequest,
    db: Session = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> dict:
    if payload.runner_id != runner.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="runner id mismatch")

    run = db.scalar(select(Run).where(Run.id == run_id))
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    node = db.scalar(select(RunNode).where(and_(RunNode.id == payload.node_record_id, RunNode.run_id == run_id)))
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="node not found")

    node.result_json = payload.result
    if payload.outcome == "failed":
        node.status = "failed"
    else:
        node.status = "succeeded"

    add_run_event(
        db,
        run.id,
        group=node.group_name,
        task_id=node.node_id,
        stage="task",
        event="NODE_RESULT",
        level="info" if node.status == "succeeded" else "error",
        message="node completed",
        meta={"status": node.status, "result": payload.result},
    )

    gate_info = None
    if node.kind == "gate":
        decision = payload.decision or "pass"
        gate_info = apply_gate_decision(db, run, node, decision)

    maybe_finalize_run(db, run)
    db.commit()

    return {"ok": True, "status": node.status, "gate": gate_info}


@router.post("/runs/{run_id}/fail")
def fail_node(
    run_id: str,
    payload: RunnerNodeFailRequest,
    db: Session = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> dict:
    if payload.runner_id != runner.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="runner id mismatch")

    run = db.scalar(select(Run).where(Run.id == run_id))
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    node = db.scalar(select(RunNode).where(and_(RunNode.id == payload.node_record_id, RunNode.run_id == run_id)))
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="node not found")

    node.status = "failed"
    node.result_json = {"reason": payload.reason, "details": payload.details}

    add_run_event(
        db,
        run.id,
        group=node.group_name,
        task_id=node.node_id,
        stage="task",
        event="NODE_RESULT",
        level="error",
        message="node failed",
        meta={"reason": payload.reason, "details": payload.details},
    )

    maybe_finalize_run(db, run)
    db.commit()
    return {"ok": True}
