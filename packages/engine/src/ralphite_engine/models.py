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


class RunPersistenceState(BaseModel):
    run_id: str
    status: str
    plan_path: str
    run: RunViewState
    loop_counts: dict[str, int] = Field(default_factory=dict)
    last_seq: int = 0
    updated_at: str = Field(default_factory=utc_now_iso)


class EventJournalRecord(BaseModel):
    seq: int
    ts: str
    run_id: str
    payload: dict[str, Any]


class RunCheckpoint(BaseModel):
    run_id: str
    status: str
    plan_path: str
    last_seq: int
    loop_counts: dict[str, int] = Field(default_factory=dict)
    retry_count: int = 0
    node_attempts: dict[str, int] = Field(default_factory=dict)
    node_statuses: dict[str, str] = Field(default_factory=dict)
    active_node_id: str | None = None
    updated_at: str = Field(default_factory=utc_now_iso)


class PlanDraftState(BaseModel):
    id: str
    path: str
    updated_at: str = Field(default_factory=utc_now_iso)
    title: str = "Untitled Draft"
    content: str
    autosave: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


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


class RunMetrics(BaseModel):
    compile_seconds: float = 0.0
    execution_seconds: float = 0.0
    cleanup_seconds: float = 0.0
    total_seconds: float = 0.0
    node_status_counts: dict[str, int] = Field(default_factory=dict)
    node_role_counts: dict[str, int] = Field(default_factory=dict)
    failure_reason_counts: dict[str, int] = Field(default_factory=dict)
    retry_count: int = 0


class HistoryIndex(BaseModel):
    runs: list[RunViewState] = Field(default_factory=list)


class PaletteCommand(BaseModel):
    id: str
    title: str
    scope: str = "global"
    description: str = ""
    shortcut: str | None = None
