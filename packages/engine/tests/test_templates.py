from __future__ import annotations

from pathlib import Path

import yaml

from ralphite_engine import LocalOrchestrator, seed_starter_if_missing, validate_plan_content
from ralphite_engine.templates import dump_yaml, make_starter_plan, make_starter_task_markdown


def test_seed_starter_creates_v2_when_only_legacy_plan_exists(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    legacy = {
        "version": 1,
        "plan_id": "legacy",
        "name": "legacy",
    }
    (plans_dir / "sample.yaml").write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")

    seeded = seed_starter_if_missing(plans_dir)

    assert seeded is not None
    assert seeded.exists()
    valid, issues, _summary = validate_plan_content(seeded.read_text(encoding="utf-8"), workspace_root=tmp_path)
    assert valid is True, issues


def test_orchestrator_prefers_parseable_v2_default_plan(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "RALPHEX_TASK.md").write_text(make_starter_task_markdown(), encoding="utf-8")

    v2_plan = make_starter_plan()
    (plans_dir / "valid-v2.yaml").write_text(dump_yaml(v2_plan), encoding="utf-8")
    (plans_dir / "sample.yaml").write_text(
        yaml.safe_dump({"version": 1, "plan_id": "legacy", "name": "legacy"}, sort_keys=False),
        encoding="utf-8",
    )

    orch = LocalOrchestrator(tmp_path)
    requirements = orch.collect_requirements()

    assert "tool:*" in requirements["tools"]
