from __future__ import annotations

from ralphite_engine.task_parser import parse_plan_tasks
from ralphite_schemas.plan_v4 import PlanSpecV4


def _plan(tasks: list[dict]) -> PlanSpecV4:
    return PlanSpecV4.model_validate(
        {
            "version": 4,
            "plan_id": "parser",
            "name": "parser",
            "run": {
                "pre_orchestrator": {"enabled": False, "agent": "orchestrator_pre_default"},
                "post_orchestrator": {"enabled": True, "agent": "orchestrator_post_default"},
            },
            "agents": [
                {"id": "worker_default", "role": "worker", "provider": "openai", "model": "gpt-4.1-mini"},
                {
                    "id": "orchestrator_pre_default",
                    "role": "orchestrator_pre",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                },
                {
                    "id": "orchestrator_post_default",
                    "role": "orchestrator_post",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                },
            ],
            "tasks": tasks,
        }
    )


def test_parse_plan_tasks_reads_yaml_tasks() -> None:
    plan = _plan(
        [
            {"id": "t1", "title": "Plan", "completed": False},
            {"id": "t2", "title": "Build", "completed": False, "parallel_group": 1, "deps": ["t1"]},
            {"id": "t3", "title": "Ship", "completed": True, "deps": ["t2"]},
        ]
    )

    tasks, issues = parse_plan_tasks(plan)
    assert issues == []
    assert len(tasks) == 3
    assert tasks[0].id == "t1"
    assert tasks[1].parallel_group == 1
    assert tasks[1].depends_on == ["t1"]
    assert tasks[2].completed is True


def test_parse_plan_tasks_enforces_duplicate_ids_and_deps() -> None:
    plan = _plan(
        [
            {"id": "t1", "title": "A", "completed": False, "parallel_group": 1},
            {"id": "t1", "title": "B", "completed": False, "deps": ["missing"]},
            {"id": "t2", "title": "C", "completed": False, "deps": ["missing"]},
        ]
    )

    tasks, issues = parse_plan_tasks(plan)
    assert len(tasks) == 2
    assert any("duplicate task id" in issue for issue in issues)
