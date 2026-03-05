from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from ralphite.engine.validation import validate_plan_content


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
PLAN_FIXTURES = FIXTURE_ROOT / "plans"
CONFIG_FIXTURES = FIXTURE_ROOT / "configs"

VALID_FIXTURES = [
    "general_sps_minimal.yaml",
    "branched_two_lane.yaml",
    "blue_red_per_task.yaml",
    "custom_linear_cells.yaml",
]


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_valid_plan_fixtures_resolve_cells_and_nodes(fixture_name: str) -> None:
    plan_path = PLAN_FIXTURES / fixture_name
    content = plan_path.read_text(encoding="utf-8")
    valid, issues, summary = validate_plan_content(content, plan_path=str(plan_path))
    assert valid is True, issues

    resolved = summary.get("resolved_execution", {})
    assert isinstance(resolved, dict)
    cells = resolved.get("resolved_cells", [])
    nodes = resolved.get("resolved_nodes", [])
    assert isinstance(cells, list) and len(cells) > 0
    assert isinstance(nodes, list) and len(nodes) > 0
    assert isinstance(summary.get("cell_counts"), dict)


def test_invalid_v1_fixture_surfaces_routing_diagnostics() -> None:
    plan_path = PLAN_FIXTURES / "invalid_v1_routing.yaml"
    valid, issues, summary = validate_plan_content(
        plan_path.read_text(encoding="utf-8"), plan_path=str(plan_path)
    )
    assert valid is False
    assert any(
        str(issue.get("code")) in {"tasks.unassigned", "tasks.routing.missing"}
        for issue in issues
    )
    assert summary.get("expected_version", 1) == 1


@pytest.mark.parametrize("name", ["default_profile.toml", "strict_profile.toml"])
def test_config_fixtures_are_parseable(name: str) -> None:
    path = CONFIG_FIXTURES / name
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert "profile" in parsed
    assert "policy" in parsed
    assert "run" in parsed
