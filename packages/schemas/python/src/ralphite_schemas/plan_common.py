from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceSpec(BaseModel):
    root: str | None = None


class MaterialsAutodiscoverSpec(BaseModel):
    enabled: bool = True
    path: str = ".ralphite/plans"
    include_globs: list[str] = Field(default_factory=lambda: ["**/*.yaml", "**/*.yml"])


class MaterialsSpec(BaseModel):
    autodiscover: MaterialsAutodiscoverSpec = Field(
        default_factory=MaterialsAutodiscoverSpec
    )
    includes: list[str] = Field(default_factory=list)
    uploads: list[str] = Field(default_factory=list)


class RequiredArtifactSpec(BaseModel):
    id: str
    format: str


class OutputsSpec(BaseModel):
    required_artifacts: list[RequiredArtifactSpec] = Field(default_factory=list)
