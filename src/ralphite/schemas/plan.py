from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .plan_common import MaterialsSpec, OutputsSpec, WorkspaceSpec


class AgentRole(str, Enum):
    WORKER = "worker"
    ORCHESTRATOR = "orchestrator"


class AgentProvider(str, Enum):
    CODEX = "codex"
    CURSOR = "cursor"


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentSpec(BaseModel):
    id: str
    role: AgentRole
    provider: AgentProvider = AgentProvider.CODEX
    model: str = "gpt-5.3-codex"
    reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    system_prompt: str = ""
    tools_allow: list[str] = Field(default_factory=list)


class ConstraintsSpec(BaseModel):
    max_runtime_seconds: int = Field(default=5400, ge=1)
    max_total_steps: int = Field(default=250, ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("25.00"), ge=Decimal("0"))
    fail_fast: bool = True
    max_parallel: int = Field(default=3, ge=1)
    acceptance_timeout_seconds: int = Field(default=120, ge=1)
    max_retries_per_node: int = Field(default=0, ge=0)


class TaskRoutingSpec(BaseModel):
    lane: str | None = None
    cell: str | None = None
    group: str | None = None
    team_mode: str | None = None
    tags: list[str] = Field(default_factory=list)


class TaskAcceptanceArtifactSpec(BaseModel):
    id: str
    path_glob: str
    format: str


class TaskAcceptanceSpec(BaseModel):
    commands: list[str] = Field(default_factory=list)
    required_artifacts: list[TaskAcceptanceArtifactSpec] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)


class TaskWritePolicySpec(BaseModel):
    allowed_write_roots: list[str] = Field(default_factory=list)
    forbidden_write_roots: list[str] = Field(default_factory=list)
    allow_plan_edits: bool = False
    allow_root_writes: bool = False


class TaskSpec(BaseModel):
    id: str
    title: str
    completed: bool
    description: str = ""
    deps: list[str] = Field(default_factory=list)
    agent: str | None = None
    routing: TaskRoutingSpec = Field(default_factory=TaskRoutingSpec)
    acceptance: TaskAcceptanceSpec = Field(default_factory=TaskAcceptanceSpec)
    write_policy: TaskWritePolicySpec = Field(default_factory=TaskWritePolicySpec)


class BehaviorKind(str, Enum):
    MERGE_AND_CONFLICT_RESOLUTION = "merge_and_conflict_resolution"
    SUMMARIZE_WORK = "summarize_work"
    PREPARE_DISPATCH = "prepare_dispatch"
    CUSTOM = "custom"


class BehaviorSpec(BaseModel):
    id: str
    kind: BehaviorKind
    agent: str | None = None
    prompt_template: str | None = None
    enabled: bool = True


class OrchestrationTemplate(str, Enum):
    GENERAL_SPS = "general_sps"
    BRANCHED = "branched"
    BLUE_RED = "blue_red"
    CUSTOM = "custom"


class InferenceMode(str, Enum):
    MIXED = "mixed"


class BranchedSpec(BaseModel):
    lanes: list[str] = Field(default_factory=list)


class BlueRedSpec(BaseModel):
    loop_unit: str = "per_task"


class CustomCellKind(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    ORCHESTRATOR = "orchestrator"
    SPLIT = "split"
    JOIN = "join"
    TEAM_CYCLE = "team_cycle"


class CustomCellSpec(BaseModel):
    id: str
    kind: CustomCellKind
    task_ids: list[str] = Field(default_factory=list)
    behavior: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    lane: str | None = None
    team: str | None = None


class CustomOrchestrationSpec(BaseModel):
    cells: list[CustomCellSpec] = Field(default_factory=list)


class OrchestrationSpec(BaseModel):
    template: OrchestrationTemplate
    inference_mode: InferenceMode = InferenceMode.MIXED
    behaviors: list[BehaviorSpec] = Field(default_factory=list)
    branched: BranchedSpec = Field(default_factory=BranchedSpec)
    blue_red: BlueRedSpec = Field(default_factory=BlueRedSpec)
    custom: CustomOrchestrationSpec = Field(default_factory=CustomOrchestrationSpec)


class PlanSpec(BaseModel):
    version: Literal[1]
    plan_id: str
    name: str
    agent_defaults_ref: str | None = None
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    materials: MaterialsSpec = Field(default_factory=MaterialsSpec)
    constraints: ConstraintsSpec = Field(default_factory=ConstraintsSpec)
    agents: list[AgentSpec] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(default_factory=list)
    orchestration: OrchestrationSpec
    outputs: OutputsSpec = Field(default_factory=OutputsSpec)


class AgentDefaultsSpec(BaseModel):
    version: Literal[1]
    agents: list[AgentSpec] = Field(default_factory=list)
    behaviors: list[BehaviorSpec] = Field(default_factory=list)
