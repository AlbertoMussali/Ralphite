from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ralphite_api.api.deps import get_current_user
from ralphite_api.db.session import SessionLocal, get_db
from ralphite_api.models import Project, Run, RunArtifact, RunEvent, RunNode, User
from ralphite_api.schemas.runs import (
    CreateRunRequest,
    RunBundleResponse,
    RunDetailResponse,
    RunResponse,
    ValidatePlanRequest,
    ValidatePlanResponse,
)
from ralphite_api.services.plan_service import parse_plan_yaml, validate_and_compile
from ralphite_api.services.run_service import (
    add_run_event,
    create_run_nodes,
    find_run_plan_content,
    make_permission_snapshot,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["runs"])


def _assert_project_owner(db: Session, project_id: str, user_id: str) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if project.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not project owner")
    return project


@router.post("/plans/validate", response_model=ValidatePlanResponse)
def validate_plan(
    project_id: str,
    payload: ValidatePlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ValidatePlanResponse:
    _assert_project_owner(db, project_id, user.id)

    valid, issues, summary, diagnostics = validate_and_compile(payload.content)
    return ValidatePlanResponse(valid=valid, issues=issues, summary=summary, diagnostics=diagnostics)


@router.post("/runs", response_model=RunResponse)
def create_run(
    project_id: str,
    payload: CreateRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunResponse:
    _assert_project_owner(db, project_id, user.id)

    try:
        plan_content, plan_file_id = find_run_plan_content(db, payload.model_dump())
        valid, issues, summary, _diagnostics = validate_and_compile(plan_content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"issues": issues})

    plan = parse_plan_yaml(plan_content)

    run = Run(
        project_id=project_id,
        plan_file_id=plan_file_id,
        plan_spec=plan.model_dump(mode="json", by_alias=True),
        status="running",
        started_at=datetime.now(UTC),
        metadata_json={"validation_summary": summary},
    )
    db.add(run)
    db.flush()

    create_run_nodes(db, run)
    make_permission_snapshot(db, project_id, run.id)

    add_run_event(
        db,
        run.id,
        group=None,
        task_id=None,
        stage="plan",
        event="RUN_PLAN_READY",
        level="info",
        message="run plan ready",
        meta=summary,
    )
    add_run_event(
        db,
        run.id,
        group=None,
        task_id=None,
        stage="plan",
        event="RUN_STARTED",
        level="info",
        message="run started",
        meta={"project_id": project_id},
    )

    db.commit()
    db.refresh(run)

    return RunResponse(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunDetailResponse:
    _assert_project_owner(db, project_id, user.id)

    run = db.scalar(select(Run).where(and_(Run.id == run_id, Run.project_id == project_id)))
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    nodes = db.scalars(select(RunNode).where(RunNode.run_id == run.id).order_by(RunNode.created_at.asc())).all()

    return RunDetailResponse(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        metadata_json=run.metadata_json,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        nodes=[
            {
                "id": node.id,
                "node_id": node.node_id,
                "group": node.group_name,
                "kind": node.kind,
                "status": node.status,
                "attempt_count": node.attempt_count,
                "depends_on": node.depends_on,
                "result": node.result_json,
            }
            for node in nodes
        ],
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _assert_project_owner(db, project_id, user.id)

    run = db.scalar(select(Run).where(and_(Run.id == run_id, Run.project_id == project_id)))
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    if run.status in {"succeeded", "failed", "cancelled", "timed_out"}:
        return {"ok": True, "status": run.status}

    run.status = "cancelled"
    run.completed_at = datetime.now(UTC)

    nodes = db.scalars(select(RunNode).where(RunNode.run_id == run.id)).all()
    for node in nodes:
        if node.status in {"queued", "running"}:
            node.status = "blocked"

    add_run_event(
        db,
        run.id,
        group=None,
        task_id=None,
        stage="summary",
        event="RUN_DONE",
        level="warn",
        message="run cancelled",
        meta={"status": "cancelled"},
    )

    db.commit()
    return {"ok": True, "status": "cancelled"}


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    project_id: str,
    run_id: str,
    request: Request,
    after_id: int = Query(default=0),
    db: Session = Depends(get_db),
):
    run = db.scalar(select(Run).where(and_(Run.id == run_id, Run.project_id == project_id)))
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    async def event_stream():
        last_id = after_id
        while True:
            if await request.is_disconnected():
                break

            with SessionLocal() as sse_db:
                rows = sse_db.scalars(
                    select(RunEvent)
                    .where(and_(RunEvent.run_id == run_id, RunEvent.id > last_id))
                    .order_by(RunEvent.id.asc())
                    .limit(100)
                ).all()

                if rows:
                    for row in rows:
                        payload = {
                            "id": row.id,
                            "ts": row.ts.isoformat(),
                            "run_id": row.run_id,
                            "group": row.group_name,
                            "task_id": row.task_id,
                            "stage": row.stage,
                            "event": row.event,
                            "level": row.level,
                            "message": row.message,
                            "meta": row.meta,
                        }
                        last_id = row.id
                        yield f"event: run_event\ndata: {json.dumps(payload)}\n\n"
                else:
                    yield ": keepalive\n\n"

            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/runs/{run_id}/bundle", response_model=RunBundleResponse)
def get_run_bundle(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunBundleResponse:
    _assert_project_owner(db, project_id, user.id)

    run = db.scalar(select(Run).where(and_(Run.id == run_id, Run.project_id == project_id)))
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    artifacts = db.scalars(select(RunArtifact).where(RunArtifact.run_id == run.id).order_by(RunArtifact.created_at.asc())).all()

    return RunBundleResponse(
        run_id=run.id,
        artifacts=[
            {
                "id": artifact.id,
                "artifact_id": artifact.artifact_id,
                "format": artifact.format,
                "content": artifact.content,
                "created_at": artifact.created_at.isoformat(),
            }
            for artifact in artifacts
        ],
    )
