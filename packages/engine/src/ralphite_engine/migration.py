from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ralphite_engine.validation import UNSUPPORTED_VERSION_MESSAGE, validate_plan_content


@dataclass
class MigrationResult:
    source: Path
    destination: Path | None
    changed: bool
    warnings: list[str]


@dataclass
class StrictMigrationResult:
    source: Path
    changed: bool
    valid: bool
    warnings: list[str]
    issues: list[dict[str, Any]]


def _load(path: Path) -> dict[str, Any] | None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    return raw


def _workspace_root_for_plan(path: Path) -> Path:
    return path.parent.parent.parent if path.parent.name == "plans" else path.parent


def migrate_plan_file(path: Path, out_dir: Path) -> MigrationResult:
    source = path.resolve()
    raw = _load(source)
    if raw is None:
        return MigrationResult(source=source, destination=None, changed=False, warnings=["plan root is not a mapping"])

    version = int(raw.get("version", 1))
    if version != 3:
        return MigrationResult(
            source=source,
            destination=None,
            changed=False,
            warnings=[UNSUPPORTED_VERSION_MESSAGE, "automatic migration has been removed"],
        )

    valid, issues, _summary = validate_plan_content(source.read_text(encoding="utf-8"), workspace_root=_workspace_root_for_plan(source))
    warnings = [] if valid else [f"validation issue: {issue.get('code')} {issue.get('message')}" for issue in issues]
    return MigrationResult(source=source, destination=None, changed=False, warnings=warnings)


def migrate_plan_in_place(path: Path) -> StrictMigrationResult:
    source = path.resolve()
    raw = _load(source)
    if raw is None:
        return StrictMigrationResult(
            source=source,
            changed=False,
            valid=False,
            warnings=["plan root is not a mapping"],
            issues=[{"code": "yaml.invalid", "message": "plan root is not a mapping", "path": "root", "level": "error"}],
        )

    version = int(raw.get("version", 1))
    if version != 3:
        return StrictMigrationResult(
            source=source,
            changed=False,
            valid=False,
            warnings=[UNSUPPORTED_VERSION_MESSAGE, "automatic migration has been removed"],
            issues=[
                {
                    "code": "version.unsupported",
                    "message": UNSUPPORTED_VERSION_MESSAGE,
                    "path": "version",
                    "level": "error",
                }
            ],
        )

    content = source.read_text(encoding="utf-8")
    workspace_root = _workspace_root_for_plan(source)
    valid, issues, _summary = validate_plan_content(content, workspace_root=workspace_root)

    return StrictMigrationResult(
        source=source,
        changed=False,
        valid=valid,
        warnings=[],
        issues=issues,
    )
