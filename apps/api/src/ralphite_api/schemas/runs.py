from datetime import datetime

from pydantic import BaseModel, Field


class ValidatePlanRequest(BaseModel):
    content: str


class ValidationIssueResponse(BaseModel):
    code: str
    message: str
    path: str
    level: str
    hint: str | None = None


class ValidationSummaryResponse(BaseModel):
    plan_id: str | None = None
    name: str | None = None
    nodes: int = 0
    edges: int = 0
    agent_nodes: int = 0
    gate_nodes: int = 0
    groups: dict[str, int] = Field(default_factory=dict)
    loops: list[dict] = Field(default_factory=list)
    parallel_sets: list[dict] = Field(default_factory=list)
    constraints: dict = Field(default_factory=dict)
    required_tools: list[str] = Field(default_factory=list)
    required_mcps: list[str] = Field(default_factory=list)


class ValidationDiagnosticsResponse(BaseModel):
    empty_plan: bool = False
    no_agent_nodes: bool = False
    no_outputs: bool = False
    no_retry_loop: bool = False
    single_node_only: bool = False
    readable_messages: list[str] = Field(default_factory=list)


class ValidatePlanResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssueResponse]
    summary: ValidationSummaryResponse
    diagnostics: ValidationDiagnosticsResponse


class CreateRunRequest(BaseModel):
    plan_file_id: str | None = None
    plan_content: str | None = None


class RunResponse(BaseModel):
    id: str
    project_id: str
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunDetailResponse(BaseModel):
    id: str
    project_id: str
    status: str
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    nodes: list[dict] = Field(default_factory=list)


class RunBundleResponse(BaseModel):
    run_id: str
    artifacts: list[dict] = Field(default_factory=list)
