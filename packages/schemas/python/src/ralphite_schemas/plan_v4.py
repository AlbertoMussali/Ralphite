from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from .plan_common import MaterialsSpec, OutputsSpec, WorkspaceSpec


class AgentRole(str, Enum):
    WORKER = "worker"
    ORCHESTRATOR_PRE = "orchestrator_pre"
    ORCHESTRATOR_POST = "orchestrator_post"


class AgentSpec(BaseModel):
    id: str
    role: AgentRole
    provider: str
    model: str
    system_prompt: str = ""
    tools_allow: list[str] = Field(default_factory=list)


class OrchestratorConfigSpec(BaseModel):
    enabled: bool
    agent: str


class RunSpec(BaseModel):
    pre_orchestrator: OrchestratorConfigSpec = Field(
        default_factory=lambda: OrchestratorConfigSpec(enabled=False, agent="orchestrator_pre_default")
    )
    post_orchestrator: OrchestratorConfigSpec = Field(
        default_factory=lambda: OrchestratorConfigSpec(enabled=True, agent="orchestrator_post_default")
    )


class ConstraintsSpecV4(BaseModel):
    max_runtime_seconds: int = Field(default=5400, ge=1)
    max_total_steps: int = Field(default=250, ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("25.00"), ge=Decimal("0"))
    fail_fast: bool = True
    max_parallel: int = Field(default=3, ge=1)


class TaskSpec(BaseModel):
    id: str
    title: str
    completed: bool
    description: str = ""
    parallel_group: int = Field(default=0, ge=0)
    deps: list[str] = Field(default_factory=list)
    agent: str | None = None


class PlanSpecV4(BaseModel):
    version: int = Field(default=4)
    plan_id: str
    name: str
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    materials: MaterialsSpec = Field(default_factory=MaterialsSpec)
    run: RunSpec = Field(default_factory=RunSpec)
    constraints: ConstraintsSpecV4 = Field(default_factory=ConstraintsSpecV4)
    agents: list[AgentSpec] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(default_factory=list)
    outputs: OutputsSpec = Field(default_factory=OutputsSpec)
