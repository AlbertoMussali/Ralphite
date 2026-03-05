from __future__ import annotations

import json
from pathlib import Path

from ralphite.schemas.plan import AgentDefaultsSpec, BehaviorKind


def _load_schema() -> dict:
    root = Path(__file__).resolve().parents[2]
    schema_path = (
        root / "src" / "ralphite" / "schemas" / "json" / "agent-defaults.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def test_agent_defaults_model_and_json_schema_are_aligned() -> None:
    schema = _load_schema()
    required = schema.get("required")
    assert isinstance(required, list)
    assert set(required) == {"version", "agents", "behaviors"}

    model_fields = set(AgentDefaultsSpec.model_fields.keys())
    assert {"version", "agents", "behaviors"}.issubset(model_fields)

    behavior_kind_enum = schema["properties"]["behaviors"]["items"]["properties"][
        "kind"
    ]["enum"]
    assert set(behavior_kind_enum) == {item.value for item in BehaviorKind}
    assert schema["properties"]["version"]["const"] == 1
