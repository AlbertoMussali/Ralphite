from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class NodeKind(str, Enum):
    AGENT = "agent"
    GATE = "gate"


class EdgeWhen(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    ALWAYS = "always"


class WorkspaceSpec(BaseModel):
    root: str | None = None


class MaterialsAutodiscoverSpec(BaseModel):
    enabled: bool = True
    path: str = ".ralphite/plans"
    include_globs: list[str] = Field(default_factory=lambda: ["**/*.yaml", "**/*.yml"])


class MaterialsSpec(BaseModel):
    autodiscover: MaterialsAutodiscoverSpec = Field(default_factory=MaterialsAutodiscoverSpec)
    includes: list[str] = Field(default_factory=list)
    uploads: list[str] = Field(default_factory=list)


class AgentSpec(BaseModel):
    id: str
    provider: str
    model: str
    system_prompt: str = ""
    tools_allow: list[str] = Field(default_factory=list)


class GateSpec(BaseModel):
    mode: str
    pass_if: str


class NodeSpec(BaseModel):
    id: str
    kind: NodeKind
    group: str = "default"
    depends_on: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    task: str | None = None
    gate: GateSpec | None = None


class EdgeSpec(BaseModel):
    from_node: str = Field(alias="from")
    to: str
    when: EdgeWhen = EdgeWhen.SUCCESS
    loop_id: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class LoopSpec(BaseModel):
    id: str
    max_iterations: int = Field(default=1, ge=1)


class GraphSpec(BaseModel):
    nodes: list[NodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)
    loops: list[LoopSpec] = Field(default_factory=list)


class ConstraintsSpec(BaseModel):
    max_runtime_seconds: int = Field(default=5400, ge=1)
    max_total_steps: int = Field(default=250, ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("25.00"), ge=Decimal("0"))
    fail_fast: bool = True


class RequiredArtifactSpec(BaseModel):
    id: str
    format: str


class OutputsSpec(BaseModel):
    required_artifacts: list[RequiredArtifactSpec] = Field(default_factory=list)


class PlanSpecV1(BaseModel):
    version: int = Field(default=1)
    plan_id: str
    name: str
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    materials: MaterialsSpec = Field(default_factory=MaterialsSpec)
    agents: list[AgentSpec]
    graph: GraphSpec
    constraints: ConstraintsSpec = Field(default_factory=ConstraintsSpec)
    outputs: OutputsSpec = Field(default_factory=OutputsSpec)
