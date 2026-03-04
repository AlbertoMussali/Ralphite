from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CliOutputEnvelopeV1(BaseModel):
    schema_version: Literal["cli-output.v1"] = "cli-output.v1"
    command: str
    ok: bool
    status: str
    run_id: str | None = None
    exit_code: int = 0
    issues: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
