from __future__ import annotations

import yaml

from ralphite_engine.editor import plan_to_rows, rows_to_plan_data
from ralphite_engine.templates import make_starter_plan
from ralphite_engine.validation import parse_plan_yaml, validate_plan_content


def test_editor_round_trip_stays_schema_valid() -> None:
    source = make_starter_plan()
    source_yaml = yaml.safe_dump(source, sort_keys=False, allow_unicode=False)
    model = parse_plan_yaml(source_yaml)

    rows = plan_to_rows(model)
    rows_data = rows_to_plan_data(rows)
    output_yaml = yaml.safe_dump(rows_data, sort_keys=False, allow_unicode=False)

    valid, issues, _summary = validate_plan_content(output_yaml)
    assert valid, issues


def test_editor_rows_allow_simple_mutation() -> None:
    source = make_starter_plan()
    source_yaml = yaml.safe_dump(source, sort_keys=False, allow_unicode=False)
    model = parse_plan_yaml(source_yaml)

    rows = plan_to_rows(model)
    steps = rows["steps"]
    steps[0].task = "Updated task"

    out = rows_to_plan_data(rows)
    assert out["graph"]["nodes"][0]["task"] == "Updated task"
