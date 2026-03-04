from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ralphite_api.api.deps import get_current_user
from ralphite_api.db.session import get_db
from ralphite_api.models import PlanFile, Project, Runner, RunnerCapability, ToolPolicy, User, WorkspaceConnection
from ralphite_api.schemas.projects import (
    ConnectWorkspaceRunnerRequest,
    CreateProjectRequest,
    PlanContentResponse,
    PlanFileResponse,
    ProjectResponse,
    SaveVersionedPlanRequest,
    ToolPolicyRequest,
    ToolPolicyResponse,
    UploadPlanRequest,
    WorkspaceConnectRequest,
    WorkspaceConnectionResponse,
)
from ralphite_api.services.plan_sync import sync_plan_files
from ralphite_api.services.plan_templates import (
    dump_plan_yaml,
    make_starter_plan_dict,
    versioned_filename,
    workspace_relative_path,
    write_workspace_plan,
)
from ralphite_schemas.plan import PlanSpecV1

router = APIRouter(prefix="/projects", tags=["projects"])


def _assert_project_owner(db: Session, project_id: str, user_id: str) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if project.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not project owner")
    return project


def ensure_default_project(db: Session, user: User) -> Project:
    project = db.scalar(
        select(Project).where(Project.user_id == user.id, Project.is_default.is_(True)).order_by(Project.created_at.asc())
    )
    if project:
        return project

    existing = db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.created_at.asc())).all()
    if existing:
        project = existing[0]
        project.is_default = True
    else:
        project = Project(user_id=user.id, name="Default Workspace", is_default=True)
        db.add(project)
        db.flush()

    policy = db.scalar(select(ToolPolicy).where(ToolPolicy.project_id == project.id))
    if not policy:
        db.add(
            ToolPolicy(
                project_id=project.id,
                allow_tools=["tool:*"],
                deny_tools=[],
                allow_mcps=["mcp:*"],
                deny_mcps=[],
            )
        )
    db.commit()
    db.refresh(project)
    return project


def _serialize_plan_file(row: PlanFile) -> PlanFileResponse:
    return PlanFileResponse(
        id=row.id,
        source=row.source,
        origin=row.origin,
        path=row.path,
        filename=row.filename,
        checksum_sha256=row.checksum_sha256,
        version_label=row.version_label,
        modified_at=row.modified_at,
        updated_at=row.updated_at,
    )


def _serialize_workspace(row: WorkspaceConnection) -> WorkspaceConnectionResponse:
    return WorkspaceConnectionResponse(
        project_id=row.project_id,
        workspace_root=row.workspace_root,
        connected_runner_id=row.connected_runner_id,
        runner_id=row.runner_id,
        status=row.status,
        bootstrap_state=row.bootstrap_state,
        updated_at=row.updated_at,
    )


@router.post("", response_model=ProjectResponse)
def create_project(
    payload: CreateProjectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = Project(user_id=user.id, name=payload.name)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectResponse.model_validate(project, from_attributes=True)


@router.post("/default", response_model=ProjectResponse)
def ensure_default_project_endpoint(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = ensure_default_project(db, user)
    return ProjectResponse.model_validate(project, from_attributes=True)


@router.get("", response_model=list[ProjectResponse])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ProjectResponse]:
    rows = db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.created_at.desc())).all()
    return [ProjectResponse.model_validate(row, from_attributes=True) for row in rows]


@router.post("/{project_id}/workspace/connect", response_model=WorkspaceConnectionResponse)
def connect_workspace(
    project_id: str,
    payload: WorkspaceConnectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkspaceConnectionResponse:
    _assert_project_owner(db, project_id, user.id)

    row = db.scalar(select(WorkspaceConnection).where(WorkspaceConnection.project_id == project_id))
    if not row:
        row = WorkspaceConnection(
            project_id=project_id,
            workspace_root=payload.workspace_root,
            status="pending",
            bootstrap_state="pending",
        )
        db.add(row)
    else:
        row.workspace_root = payload.workspace_root
        row.status = "pending"
        row.bootstrap_state = "pending"
        row.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(row)
    return _serialize_workspace(row)


@router.post("/{project_id}/workspace/connect-runner", response_model=WorkspaceConnectionResponse)
def connect_workspace_runner(
    project_id: str,
    payload: ConnectWorkspaceRunnerRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkspaceConnectionResponse:
    _assert_project_owner(db, project_id, user.id)
    runner = db.scalar(select(Runner).where(Runner.id == payload.runner_id))
    if not runner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="runner not found")

    row = db.scalar(select(WorkspaceConnection).where(WorkspaceConnection.project_id == project_id))
    if not row:
        row = WorkspaceConnection(
            project_id=project_id,
            workspace_root=runner.workspace_root,
            connected_runner_id=runner.id,
            runner_id=runner.id,
            status="connected",
            bootstrap_state="pending",
        )
        db.add(row)
    else:
        row.workspace_root = runner.workspace_root
        row.connected_runner_id = runner.id
        row.runner_id = runner.id
        row.status = "connected"
        row.updated_at = datetime.now(UTC)

    cap = db.scalar(
        select(RunnerCapability).where(RunnerCapability.runner_id == runner.id).order_by(RunnerCapability.captured_at.desc())
    )
    if cap and isinstance(cap.payload.get("plan_files"), list):
        sync_plan_files(db, project_id, cap.payload["plan_files"], source="autodiscovered")
        row.bootstrap_state = "seeded" if cap.payload.get("seeded_starter") else row.bootstrap_state

    db.commit()
    db.refresh(row)
    return _serialize_workspace(row)


@router.get("/{project_id}/plans/discovered", response_model=list[PlanFileResponse])
def discovered_plans(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PlanFileResponse]:
    _assert_project_owner(db, project_id, user.id)
    rows = db.scalars(select(PlanFile).where(PlanFile.project_id == project_id).order_by(PlanFile.updated_at.desc())).all()
    return [_serialize_plan_file(row) for row in rows]


@router.get("/{project_id}/plans/{plan_file_id}/content", response_model=PlanContentResponse)
def plan_content(
    project_id: str,
    plan_file_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanContentResponse:
    _assert_project_owner(db, project_id, user.id)
    row = db.scalar(select(PlanFile).where(PlanFile.id == plan_file_id, PlanFile.project_id == project_id))
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan file not found")
    if not row.content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="plan content unavailable")
    return PlanContentResponse(id=row.id, path=row.path, content=row.content)


@router.post("/{project_id}/plans/upload", response_model=PlanFileResponse)
def upload_plan(
    project_id: str,
    payload: UploadPlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanFileResponse:
    _assert_project_owner(db, project_id, user.id)

    digest = sha256(payload.content.encode("utf-8")).hexdigest()
    filename = PurePosixPath(payload.filename).name
    path = f"uploads/{filename}"

    row = PlanFile(
        project_id=project_id,
        source="upload",
        origin="upload",
        path=path,
        filename=filename,
        checksum_sha256=digest,
        content=payload.content,
        modified_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_plan_file(row)


@router.post("/{project_id}/plans/seed-starter", response_model=PlanFileResponse)
def seed_starter_plan(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanFileResponse:
    _assert_project_owner(db, project_id, user.id)
    workspace = db.scalar(select(WorkspaceConnection).where(WorkspaceConnection.project_id == project_id))
    if not workspace:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="workspace not connected")

    starter_plan = make_starter_plan_dict()
    content = dump_plan_yaml(starter_plan)
    filename = versioned_filename(starter_plan["plan_id"], "starter")

    path = f".ralphite/plans/{filename}"
    if workspace.workspace_root:
        try:
            written = write_workspace_plan(workspace.workspace_root, filename, content)
            path = workspace_relative_path(workspace.workspace_root, written)
            workspace.bootstrap_state = "seeded"
        except Exception:
            workspace.bootstrap_state = "error"

    row = PlanFile(
        project_id=project_id,
        source="builder",
        origin="builder",
        path=path,
        filename=filename,
        checksum_sha256=sha256(content.encode("utf-8")).hexdigest(),
        content=content,
        modified_at=datetime.now(UTC),
        version_label=filename.rsplit(".", 2)[-2] if "." in filename else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_plan_file(row)


@router.post("/{project_id}/plans/save-versioned", response_model=PlanFileResponse)
def save_versioned_plan(
    project_id: str,
    payload: SaveVersionedPlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanFileResponse:
    _assert_project_owner(db, project_id, user.id)
    workspace = db.scalar(select(WorkspaceConnection).where(WorkspaceConnection.project_id == project_id))
    if not workspace:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="workspace not connected")

    parsed = PlanSpecV1.model_validate(payload.plan)
    content = dump_plan_yaml(parsed.model_dump(mode="json", by_alias=True))
    filename = versioned_filename(parsed.plan_id, payload.filename_hint)

    try:
        written = write_workspace_plan(workspace.workspace_root, filename, content)
        path = workspace_relative_path(workspace.workspace_root, written)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"failed to write plan file: {exc}") from exc

    row = PlanFile(
        project_id=project_id,
        source="builder",
        origin="builder",
        path=path,
        filename=filename,
        checksum_sha256=sha256(content.encode("utf-8")).hexdigest(),
        content=content,
        modified_at=datetime.now(UTC),
        version_label=filename.rsplit(".", 2)[-2] if "." in filename else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_plan_file(row)


@router.get("/{project_id}/capabilities")
def capabilities(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _assert_project_owner(db, project_id, user.id)

    workspace = db.scalar(select(WorkspaceConnection).where(WorkspaceConnection.project_id == project_id))
    runner_id = workspace.connected_runner_id if workspace else None
    if workspace and workspace.runner_id:
        runner_id = workspace.runner_id
    if not runner_id:
        return {"runner": None, "capabilities": None}

    cap = db.scalar(select(RunnerCapability).where(RunnerCapability.runner_id == runner_id).order_by(RunnerCapability.captured_at.desc()))
    return {"runner": runner_id, "capabilities": cap.payload if cap else None}


@router.get("/{project_id}/tool-policy", response_model=ToolPolicyResponse)
def get_tool_policy(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ToolPolicyResponse:
    _assert_project_owner(db, project_id, user.id)
    row = db.scalar(select(ToolPolicy).where(ToolPolicy.project_id == project_id))
    if not row:
        row = ToolPolicy(
            project_id=project_id,
            allow_tools=["tool:*"],
            deny_tools=[],
            allow_mcps=["mcp:*"],
            deny_mcps=[],
            updated_at=datetime.now(UTC),
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    return ToolPolicyResponse(
        project_id=row.project_id,
        allow_tools=row.allow_tools,
        deny_tools=row.deny_tools,
        allow_mcps=row.allow_mcps,
        deny_mcps=row.deny_mcps,
        updated_at=row.updated_at,
    )


@router.put("/{project_id}/tool-policy", response_model=ToolPolicyResponse)
def update_tool_policy(
    project_id: str,
    payload: ToolPolicyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ToolPolicyResponse:
    _assert_project_owner(db, project_id, user.id)

    row = db.scalar(select(ToolPolicy).where(ToolPolicy.project_id == project_id))
    if not row:
        row = ToolPolicy(project_id=project_id)
        db.add(row)

    row.allow_tools = payload.allow_tools
    row.deny_tools = payload.deny_tools
    row.allow_mcps = payload.allow_mcps
    row.deny_mcps = payload.deny_mcps
    row.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(row)

    return ToolPolicyResponse(
        project_id=row.project_id,
        allow_tools=row.allow_tools,
        deny_tools=row.deny_tools,
        allow_mcps=row.allow_mcps,
        deny_mcps=row.deny_mcps,
        updated_at=row.updated_at,
    )
