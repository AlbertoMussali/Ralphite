from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from .plan_common import MaterialsSpec, OutputsSpec, WorkspaceSpec


class TaskSourceKind(str, Enum):
    MARKDOWN_CHECKLIST = "markdown_checklist"


class AgentRole(str, Enum):
    WORKER = "worker"
    ORCHESTRATOR_PRE = "orchestrator_pre"
    ORCHESTRATOR_POST = "orchestrator_post"


class TaskSourceSpec(BaseModel):
    kind: TaskSourceKind = TaskSourceKind.MARKDOWN_CHECKLIST
    path: str = "RALPHEX_TASK.md"
    parser_version: int = 2


class AgentProfileSpec(BaseModel):
    id: str
    role: AgentRole
    provider: str
    model: str
    system_prompt: str = ""
    tools_allow: list[str] = Field(default_factory=list)


class OrchestratorStepSpec(BaseModel):
    enabled: bool
    agent_profile_id: str


class WorkerSegmentsSpec(BaseModel):
    sequential_before: list[str] = Field(default_factory=list)
    parallel: list[str] = Field(default_factory=list)
    sequential_after: list[str] = Field(default_factory=list)


class PhaseExecutionSpec(BaseModel):
    id: str
    label: str = ""
    pre_orchestrator: OrchestratorStepSpec = Field(
        default_factory=lambda: OrchestratorStepSpec(enabled=False, agent_profile_id="orchestrator_pre_default")
    )
    workers: WorkerSegmentsSpec = Field(default_factory=WorkerSegmentsSpec)
    post_orchestrator: OrchestratorStepSpec = Field(
        default_factory=lambda: OrchestratorStepSpec(enabled=True, agent_profile_id="orchestrator_post_default")
    )


class ExecutionStructureSpec(BaseModel):
    phases: list[PhaseExecutionSpec] = Field(default_factory=list)


class ConstraintsSpecV2(BaseModel):
    max_runtime_seconds: int = Field(default=5400, ge=1)
    max_total_steps: int = Field(default=250, ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("25.00"), ge=Decimal("0"))
    fail_fast: bool = True
    max_parallel: int = Field(default=3, ge=1)


class PlanSpecV2(BaseModel):
    version: int = Field(default=2)
    plan_id: str
    name: str
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    materials: MaterialsSpec = Field(default_factory=MaterialsSpec)
    task_source: TaskSourceSpec = Field(default_factory=TaskSourceSpec)
    agent_profiles: list[AgentProfileSpec] = Field(default_factory=list)
    execution_structure: ExecutionStructureSpec = Field(default_factory=ExecutionStructureSpec)
    constraints: ConstraintsSpecV2 = Field(default_factory=ConstraintsSpecV2)
    outputs: OutputsSpec = Field(default_factory=OutputsSpec)
