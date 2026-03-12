from __future__ import annotations

import json
from pathlib import Path

from ralphite.schemas.plan import (
    BehaviorKind,
    ConstraintsSpec,
    OrchestrationTemplate,
    PlanSpec,
)


def _load_schema() -> dict:
    root = Path(__file__).resolve().parents[2]
    schema_path = (
        root / "src" / "ralphite" / "schemas" / "json" / "plan-spec.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def test_plan_v1_model_and_json_schema_are_aligned() -> None:
    schema = _load_schema()
    required = schema.get("required")
    assert isinstance(required, list)
    assert set(required) == {
        "version",
        "plan_id",
        "name",
        "materials",
        "constraints",
        "tasks",
        "orchestration",
        "outputs",
    }

    model_fields = set(PlanSpec.model_fields.keys())
    assert {
        "version",
        "plan_id",
        "name",
        "agent_defaults_ref",
        "materials",
        "constraints",
        "agents",
        "tasks",
        "orchestration",
        "outputs",
    }.issubset(model_fields)

    orchestration_props = schema["properties"]["orchestration"]["properties"]
    assert set(orchestration_props["template"]["enum"]) == {
        item.value for item in OrchestrationTemplate
    }
    behavior_kind_enum = orchestration_props["behaviors"]["items"]["properties"][
        "kind"
    ]["enum"]
    assert set(behavior_kind_enum) == {item.value for item in BehaviorKind}
    assert schema["properties"]["version"]["const"] == 1
    provider_enum = schema["properties"]["agents"]["items"]["properties"]["provider"][
        "enum"
    ]
    assert set(provider_enum) == {"codex", "cursor"}

    constraints_props = schema["properties"]["constraints"]["properties"]
    assert constraints_props["acceptance_timeout_seconds"]["default"] == 120
    assert constraints_props["max_retries_per_node"]["default"] == 0
    task_props = schema["properties"]["tasks"]["items"]["properties"]
    assert "parallel_group" not in task_props
    defaults = ConstraintsSpec()
    assert defaults.acceptance_timeout_seconds == 120
    assert defaults.max_retries_per_node == 0
