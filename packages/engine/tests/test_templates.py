from __future__ import annotations

from pathlib import Path

import yaml

from ralphite_engine import (
    LocalOrchestrator,
    seed_starter_if_missing,
    validate_plan_content,
)
from ralphite_engine.templates import dump_yaml, make_starter_plan


def test_seed_starter_creates_v5_when_only_legacy_plan_exists(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    legacy = {
        "version": 1,
        "plan_id": "legacy",
        "name": "legacy",
    }
    (plans_dir / "sample.yaml").write_text(
        yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8"
    )

    seeded = seed_starter_if_missing(plans_dir)

    assert seeded is not None
    assert seeded.exists()
    valid, issues, summary = validate_plan_content(
        seeded.read_text(encoding="utf-8"), workspace_root=tmp_path
    )
    assert valid is True, issues
    assert summary.get("version") == 5


def test_orchestrator_prefers_parseable_v5_default_plan(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    v5_plan = make_starter_plan()
    (plans_dir / "valid-v5.yaml").write_text(dump_yaml(v5_plan), encoding="utf-8")
    (plans_dir / "sample.yaml").write_text(
        yaml.safe_dump(
            {"version": 1, "plan_id": "legacy", "name": "legacy"}, sort_keys=False
        ),
        encoding="utf-8",
    )

    orch = LocalOrchestrator(tmp_path)
    requirements = orch.collect_requirements()

    assert "tool:*" in requirements["tools"]
