from __future__ import annotations

from pathlib import Path

import yaml

from ralphite.engine import (
    LocalOrchestrator,
    seed_starter_if_missing,
    validate_plan_content,
)
from ralphite.engine.templates import dump_yaml, make_starter_plan


def test_seed_starter_creates_v1_when_only_invalid_plan_exists(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    invalid_plan = {
        "version": 4,
        "plan_id": "invalid_input",
        "name": "invalid_input",
    }
    (plans_dir / "sample.yaml").write_text(
        yaml.safe_dump(invalid_plan, sort_keys=False), encoding="utf-8"
    )

    seeded = seed_starter_if_missing(plans_dir)

    assert seeded is not None
    assert seeded.exists()
    valid, issues, summary = validate_plan_content(
        seeded.read_text(encoding="utf-8"), workspace_root=tmp_path
    )
    assert valid is True, issues
    assert summary.get("version") == 1


def test_orchestrator_prefers_parseable_v1_default_plan(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    plan_candidate = make_starter_plan()
    (plans_dir / "valid-v1.yaml").write_text(
        dump_yaml(plan_candidate), encoding="utf-8"
    )
    (plans_dir / "sample.yaml").write_text(
        yaml.safe_dump(
            {"version": 4, "plan_id": "invalid_input", "name": "invalid_input"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    orch = LocalOrchestrator(tmp_path)
    requirements = orch.collect_requirements()

    assert "tool:*" in requirements["tools"]
