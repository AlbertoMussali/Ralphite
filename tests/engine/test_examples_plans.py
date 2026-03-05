from __future__ import annotations

from pathlib import Path

import pytest

from ralphite.engine.structure_compiler import compile_execution_structure
from ralphite.engine.task_parser import parse_plan_tasks
from ralphite.engine.validation import parse_plan_with_defaults, validate_plan_content


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "plans"


def _example_plans() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.yaml"))


@pytest.mark.parametrize("plan_path", _example_plans(), ids=lambda item: item.name)
def test_examples_plans_validate_and_compile(plan_path: Path) -> None:
    content = plan_path.read_text(encoding="utf-8")
    valid, issues, summary = validate_plan_content(
        content,
        workspace_root=REPO_ROOT,
        plan_path=str(plan_path),
    )
    assert valid is True, issues
    resolved = (
        summary.get("resolved_execution", {}) if isinstance(summary, dict) else {}
    )
    assert isinstance(resolved.get("resolved_nodes"), list)
    assert resolved.get("resolved_nodes")

    plan, _defaults_meta = parse_plan_with_defaults(
        content,
        workspace_root=REPO_ROOT,
        plan_path=str(plan_path),
    )
    tasks, parse_issues = parse_plan_tasks(plan)
    assert parse_issues == []

    compiled, compile_issues = compile_execution_structure(
        plan, tasks, task_parse_issues=parse_issues
    )
    assert compile_issues == []
    assert compiled is not None
    assert compiled.nodes
