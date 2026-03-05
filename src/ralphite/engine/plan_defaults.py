from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from ralphite.schemas.plan import AgentDefaultsSpec, BehaviorKind


@dataclass(frozen=True)
class PlanDefaultsResolutionError(Exception):
    code: str
    message: str
    path: str = "agent_defaults_ref"

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _validate_defaults_contract(defaults: AgentDefaultsSpec) -> list[str]:
    errors: list[str] = []
    agent_ids: set[str] = set()
    role_counts = {"worker": 0, "orchestrator": 0}
    for agent in defaults.agents:
        if agent.id in agent_ids:
            errors.append(f"duplicate agent id '{agent.id}'")
            continue
        agent_ids.add(agent.id)
        role_counts[agent.role.value] = role_counts.get(agent.role.value, 0) + 1

    if role_counts.get("worker", 0) == 0:
        errors.append("defaults must define at least one worker agent")
    if role_counts.get("orchestrator", 0) == 0:
        errors.append("defaults must define at least one orchestrator agent")

    behavior_ids: set[str] = set()
    enabled_kinds: set[str] = set()
    for behavior in defaults.behaviors:
        if behavior.id in behavior_ids:
            errors.append(f"duplicate behavior id '{behavior.id}'")
            continue
        behavior_ids.add(behavior.id)
        if behavior.enabled:
            enabled_kinds.add(behavior.kind.value)
        if behavior.agent and behavior.agent not in agent_ids:
            errors.append(
                f"behavior '{behavior.id}' references unknown agent '{behavior.agent}'"
            )

    baseline_kinds = {
        BehaviorKind.PREPARE_DISPATCH.value,
        BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION.value,
        BehaviorKind.SUMMARIZE_WORK.value,
    }
    for kind in sorted(baseline_kinds):
        if kind not in enabled_kinds:
            errors.append(f"defaults missing enabled baseline behavior '{kind}'")
    return errors


def _resolve_defaults_path(
    ref: str,
    *,
    workspace_root: str | Path | None,
    plan_path: str | Path | None,
) -> Path:
    candidates: list[Path] = []
    raw_path = Path(ref)
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        if plan_path is not None:
            candidates.append(Path(plan_path).expanduser().resolve().parent / raw_path)
        if workspace_root is not None:
            candidates.append(Path(workspace_root).expanduser().resolve() / raw_path)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    raise PlanDefaultsResolutionError(
        code="defaults.ref_missing",
        message=f"agent defaults file not found for ref '{ref}'",
    )


def resolve_plan_defaults(
    plan_data: dict[str, Any],
    *,
    workspace_root: str | Path | None = None,
    plan_path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = deepcopy(plan_data)
    raw_ref = str(resolved.get("agent_defaults_ref") or "").strip()
    metadata: dict[str, Any] = {
        "agent_defaults_ref": raw_ref or None,
        "resolved_path": None,
        "agents_source": "inline",
        "behaviors_source": "inline",
    }
    if not raw_ref:
        return resolved, metadata

    defaults_path = _resolve_defaults_path(
        raw_ref, workspace_root=workspace_root, plan_path=plan_path
    )
    metadata["resolved_path"] = str(defaults_path)
    try:
        raw_defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanDefaultsResolutionError(
            code="defaults.ref_unreadable",
            message=f"unable to read agent defaults file: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise PlanDefaultsResolutionError(
            code="defaults.invalid_schema",
            message=f"unable to parse agent defaults file: {exc}",
        ) from exc

    if not isinstance(raw_defaults, dict):
        raise PlanDefaultsResolutionError(
            code="defaults.invalid_schema",
            message="agent defaults document must be a YAML object",
        )

    try:
        defaults_doc = AgentDefaultsSpec.model_validate(raw_defaults)
    except PydanticValidationError as exc:
        error = exc.errors()[0] if exc.errors() else {"msg": str(exc), "loc": []}
        loc = error.get("loc", [])
        path = ".".join(str(part) for part in loc) if isinstance(loc, (tuple, list)) else ""
        detail = str(error.get("msg") or "schema validation failed")
        if path:
            detail = f"{path}: {detail}"
        raise PlanDefaultsResolutionError(
            code="defaults.invalid_schema",
            message=f"agent defaults schema invalid: {detail}",
        ) from exc

    contract_errors = _validate_defaults_contract(defaults_doc)
    if contract_errors:
        raise PlanDefaultsResolutionError(
            code="defaults.invalid_schema",
            message=contract_errors[0],
        )

    agents = resolved.get("agents")
    if not isinstance(agents, list) or len(agents) == 0:
        resolved["agents"] = [
            agent.model_dump(mode="python") for agent in defaults_doc.agents
        ]
        metadata["agents_source"] = "defaults_ref"

    orchestration = (
        resolved.get("orchestration")
        if isinstance(resolved.get("orchestration"), dict)
        else {}
    )
    if not isinstance(resolved.get("orchestration"), dict):
        resolved["orchestration"] = orchestration
    behaviors = orchestration.get("behaviors")
    if not isinstance(behaviors, list) or len(behaviors) == 0:
        orchestration["behaviors"] = [
            behavior.model_dump(mode="python") for behavior in defaults_doc.behaviors
        ]
        metadata["behaviors_source"] = "defaults_ref"

    return resolved, metadata
