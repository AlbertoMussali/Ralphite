from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    group: str | None = None
    task_id: str | None = None
    stage: str = "plan"
    event: str
    level: str = "info"
    message: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
