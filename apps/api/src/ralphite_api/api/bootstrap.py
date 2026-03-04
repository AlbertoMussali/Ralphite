from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ralphite_api.api.deps import get_current_user
from ralphite_api.db.session import get_db
from ralphite_api.models import Runner, User, WorkspaceConnection
from ralphite_api.schemas.auth import UserResponse
from ralphite_api.schemas.projects import (
    BootstrapResponse,
    RunnerCandidateResponse,
    WorkspaceStatusResponse,
)
from ralphite_api.api.projects import ensure_default_project

router = APIRouter(tags=["bootstrap"])


@router.get("/health")
def api_health() -> dict:
    return {"ok": True, "scope": "api.v1"}


def _workspace_status(db: Session, project_id: str) -> WorkspaceStatusResponse:
    row = db.scalar(select(WorkspaceConnection).where(WorkspaceConnection.project_id == project_id))
    if not row:
        return WorkspaceStatusResponse(status="not_connected")
    return WorkspaceStatusResponse(
        status=row.status,
        workspace_root=row.workspace_root,
        connected_runner_id=row.connected_runner_id or row.runner_id,
    )


@router.get("/workspaces/available", response_model=list[RunnerCandidateResponse])
def available_workspaces(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RunnerCandidateResponse]:
    # user is used for auth enforcement even though runners are local-machine scoped in v1.
    _ = user
    rows = db.scalars(select(Runner).order_by(Runner.updated_at.desc())).all()
    return [
        RunnerCandidateResponse(
            runner_id=row.id,
            workspace_root=row.workspace_root,
            status=row.status,
            runner_version=row.runner_version,
            last_heartbeat_at=row.last_heartbeat_at,
        )
        for row in rows
    ]


@router.get("/bootstrap", response_model=BootstrapResponse)
def bootstrap(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BootstrapResponse:
    project = ensure_default_project(db, user)
    runners = db.scalars(select(Runner).order_by(Runner.updated_at.desc())).all()
    candidates = [
        RunnerCandidateResponse(
            runner_id=row.id,
            workspace_root=row.workspace_root,
            status=row.status,
            runner_version=row.runner_version,
            last_heartbeat_at=row.last_heartbeat_at,
        )
        for row in runners
    ]
    return BootstrapResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            created_at=user.created_at,
            settings_json=user.settings_json,
        ).model_dump(mode="json"),
        default_project_id=project.id,
        workspace_status=_workspace_status(db, project.id),
        runner_candidates=candidates,
    )
