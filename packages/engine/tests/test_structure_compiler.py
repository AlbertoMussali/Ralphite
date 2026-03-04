from __future__ import annotations

from ralphite_engine.structure_compiler import compile_execution_structure
from ralphite_engine.task_parser import parse_plan_tasks
from ralphite_schemas.plan_v4 import PlanSpecV4


def test_compile_execution_structure_enforces_block_order() -> None:
    plan = PlanSpecV4.model_validate(
        {
            "version": 4,
            "plan_id": "block_order",
            "name": "Block Order",
            "run": {
                "pre_orchestrator": {"enabled": False, "agent": "orchestrator_pre_default"},
                "post_orchestrator": {"enabled": True, "agent": "orchestrator_post_default"},
            },
            "constraints": {"max_parallel": 3},
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
            "tasks": [
                {"id": "t1", "title": "Prep", "completed": False},
                {"id": "t2", "title": "Exec A", "completed": False, "parallel_group": 1, "deps": ["t1"]},
                {"id": "t3", "title": "Exec B", "completed": False, "parallel_group": 1, "deps": ["t1"]},
                {"id": "t4", "title": "Exec C", "completed": False, "parallel_group": 2, "deps": ["t2", "t3"]},
                {"id": "t5", "title": "Verify", "completed": False, "deps": ["t4"]},
            ],
        }
    )

    tasks, parse_issues = parse_plan_tasks(plan)
    runtime, issues = compile_execution_structure(plan, tasks, task_parse_issues=parse_issues)

    assert issues == []
    assert runtime is not None
    assert runtime.parallel_limit == 3

    levels = runtime.node_levels
    assert levels["phase-1::task::t1"] < levels["phase-1::task::t2"]
    assert levels["phase-1::task::t1"] < levels["phase-1::task::t3"]
    assert levels["phase-1::task::t2"] < levels["phase-1::task::t4"]
    assert levels["phase-1::task::t3"] < levels["phase-1::task::t4"]
    assert levels["phase-1::task::t4"] < levels["phase-1::task::t5"]

    assert [block.kind for block in runtime.blocks] == ["sequential", "parallel", "parallel", "sequential"]
