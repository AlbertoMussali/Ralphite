from datetime import datetime

from pydantic import BaseModel, Field


class PlanManifest(BaseModel):
    path: str
    checksum_sha256: str
    modified_at: datetime | None = None
    content: str | None = None


class MCPServerCapability(BaseModel):
    id: str
    tools: list[str]


class ProviderCapability(BaseModel):
    provider: str
    models: list[str]


class RunnerCapabilitiesV1(BaseModel):
    runner_id: str
    runner_version: str
    workspace_root: str
    seeded_starter: bool = False
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[MCPServerCapability] = Field(default_factory=list)
    provider_caps: list[ProviderCapability] = Field(default_factory=list)
    plan_files: list[PlanManifest] = Field(default_factory=list)


class RunnerRegisterResponse(BaseModel):
    runner_id: str
    token: str


class RunnerClaimRequest(BaseModel):
    runner_id: str


class ClaimedNodeResponse(BaseModel):
    run_id: str
    node_record_id: str
    node_id: str
    kind: str
    group: str
    attempt_count: int
    payload: dict
    permission_snapshot: dict


class RunnerNodeCompleteRequest(BaseModel):
    runner_id: str
    node_record_id: str
    result: dict = Field(default_factory=dict)
    outcome: str = "success"
    decision: str | None = None


class RunnerNodeFailRequest(BaseModel):
    runner_id: str
    node_record_id: str
    reason: str
    details: dict = Field(default_factory=dict)


class RunnerEvent(BaseModel):
    group: str | None = None
    task_id: str | None = None
    stage: str
    event: str
    level: str = "info"
    message: str
    meta: dict = Field(default_factory=dict)


class RunnerEventsBatchRequest(BaseModel):
    runner_id: str
    events: list[RunnerEvent]
