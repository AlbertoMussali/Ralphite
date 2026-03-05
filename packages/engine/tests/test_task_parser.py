from __future__ import annotations

from ralphite_engine.task_parser import parse_plan_tasks
from ralphite_schemas.plan_v5 import PlanSpecV5


def _plan(tasks: list[dict]) -> PlanSpecV5:
    return PlanSpecV5.model_validate(
        {
            "version": 5,
            "plan_id": "parser",
            "name": "parser",
            "materials": {
                "autodiscover": {"enabled": False, "path": ".", "include_globs": []},
                "includes": [],
                "uploads": [],
            },
            "constraints": {"max_parallel": 2},
            "agents": [
                {"id": "worker_default", "role": "worker", "provider": "codex", "model": "gpt-5.3-codex"},
                {
                    "id": "orchestrator_default",
                    "role": "orchestrator",
                    "provider": "codex",
                    "model": "gpt-5.3-codex",
                },
            ],
            "tasks": tasks,
            "orchestration": {
                "template": "general_sps",
                "inference_mode": "mixed",
                "behaviors": [
                    {
                        "id": "merge_default",
                        "kind": "merge_and_conflict_resolution",
                        "agent": "orchestrator_default",
                        "enabled": True,
                    }
                ],
                "branched": {"lanes": ["lane_a", "lane_b"]},
                "blue_red": {"loop_unit": "per_task"},
                "custom": {"cells": []},
            },
            "outputs": {"required_artifacts": []},
        }
    )


def test_parse_plan_tasks_reads_yaml_tasks() -> None:
    plan = _plan(
        [
            {"id": "t1", "title": "Plan", "completed": False},
            {
                "id": "t2",
                "title": "Build",
                "completed": False,
                "deps": ["t1"],
                "routing": {"lane": "lane_a", "cell": "par_core", "team_mode": None, "group": None, "tags": ["core"]},
                "acceptance": {
                    "commands": ["echo ok"],
                    "required_artifacts": [{"id": "bundle", "path_glob": "dist/*", "format": "file"}],
                    "rubric": ["build passes"],
                },
            },
            {"id": "t3", "title": "Ship", "completed": True, "deps": ["t2"]},
        ]
    )

    tasks, issues = parse_plan_tasks(plan)
    assert issues == []
    assert len(tasks) == 3
    assert tasks[0].id == "t1"
    assert tasks[1].depends_on == ["t1"]
    assert tasks[1].routing_cell == "par_core"
    assert tasks[1].routing_lane == "lane_a"
    assert tasks[1].acceptance_commands == ["echo ok"]
    assert tasks[1].acceptance_required_artifacts[0]["id"] == "bundle"
    assert tasks[2].completed is True


def test_parse_plan_tasks_enforces_duplicate_ids_and_deps() -> None:
    plan = _plan(
        [
            {"id": "t1", "title": "A", "completed": False},
            {"id": "t1", "title": "B", "completed": False, "deps": ["missing"]},
            {"id": "t2", "title": "C", "completed": False, "deps": ["missing"]},
        ]
    )

    tasks, issues = parse_plan_tasks(plan)
    assert len(tasks) == 2
    assert any("duplicate task id" in issue for issue in issues)
