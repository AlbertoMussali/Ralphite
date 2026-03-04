from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeRuntimeState(BaseModel):
    node_id: str
    kind: str
    group: str
    status: str = "queued"
    attempt_count: int = 0
    depends_on: list[str] = Field(default_factory=list)
    result: dict[str, Any] | None = None


class RunViewState(BaseModel):
    id: str
    plan_path: str
    status: str = "queued"
    created_at: str = Field(default_factory=utc_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    active_node_id: str | None = None
    retry_count: int = 0
    nodes: dict[str, NodeRuntimeState] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanDraftState(BaseModel):
    id: str
    path: str
    updated_at: str = Field(default_factory=utc_now_iso)
    title: str = "Untitled Draft"
    content: str
    autosave: bool = True


class ValidationFix(BaseModel):
    code: str
    title: str
    description: str
    path: str
    patch: dict[str, Any] = Field(default_factory=dict)


class ArtifactIndex(BaseModel):
    run_id: str
    artifacts_dir: str
    items: list[dict[str, str]] = Field(default_factory=list)


class HistoryIndex(BaseModel):
    runs: list[RunViewState] = Field(default_factory=list)
