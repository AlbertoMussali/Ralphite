from datetime import datetime

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    name: str


class ProjectResponse(BaseModel):
    id: str
    name: str
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class WorkspaceConnectRequest(BaseModel):
    workspace_root: str


class WorkspaceConnectionResponse(BaseModel):
    project_id: str
    workspace_root: str
    connected_runner_id: str | None
    runner_id: str | None = None
    status: str
    bootstrap_state: str = "pending"
    updated_at: datetime


class PlanFileResponse(BaseModel):
    id: str
    source: str
    origin: str = "autodiscovered"
    path: str
    filename: str
    checksum_sha256: str
    version_label: str | None = None
    modified_at: datetime | None
    updated_at: datetime


class UploadPlanRequest(BaseModel):
    filename: str
    content: str


class ConnectWorkspaceRunnerRequest(BaseModel):
    runner_id: str


class SaveVersionedPlanRequest(BaseModel):
    plan: dict
    filename_hint: str | None = None


class PlanContentResponse(BaseModel):
    id: str
    path: str
    content: str


class RunnerCandidateResponse(BaseModel):
    runner_id: str
    workspace_root: str
    status: str
    runner_version: str
    last_heartbeat_at: datetime | None = None


class WorkspaceStatusResponse(BaseModel):
    status: str
    workspace_root: str | None = None
    connected_runner_id: str | None = None


class BootstrapResponse(BaseModel):
    user: dict
    default_project_id: str
    workspace_status: WorkspaceStatusResponse
    runner_candidates: list[RunnerCandidateResponse]


class ToolPolicyRequest(BaseModel):
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)
    allow_mcps: list[str] = Field(default_factory=list)
    deny_mcps: list[str] = Field(default_factory=list)


class ToolPolicyResponse(BaseModel):
    project_id: str
    allow_tools: list[str]
    deny_tools: list[str]
    allow_mcps: list[str]
    deny_mcps: list[str]
    updated_at: datetime
