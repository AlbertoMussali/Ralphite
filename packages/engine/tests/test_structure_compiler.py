from __future__ import annotations

from ralphite_engine.structure_compiler import compile_execution_structure
from ralphite_engine.task_parser import parse_task_lines
from ralphite_schemas.plan_v3 import PlanSpecV3


def test_compile_execution_structure_enforces_lane_barriers() -> None:
    plan = PlanSpecV3.model_validate(
        {
            "version": 3,
            "plan_id": "lane_barrier",
            "name": "Lane Barrier",
            "task_source": {"kind": "markdown_checklist", "path": "RALPHEX_TASK.md", "parser_version": 3},
            "agent_profiles": [
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
            "execution_structure": {
                "phases": [
                    {
                        "id": "phase-1",
                        "pre_orchestrator": {"enabled": False, "agent_profile_id": "orchestrator_pre_default"},
                        "post_orchestrator": {"enabled": True, "agent_profile_id": "orchestrator_post_default"},
                    }
                ]
            },
            "constraints": {"max_parallel": 3},
        }
    )

    tasks, parse_issues = parse_task_lines(
        [
            "- [ ] Prep <!-- id:t1 phase:phase-1 lane:seq_pre agent_profile:worker_default -->",
            "- [ ] Exec A <!-- id:t2 phase:phase-1 lane:parallel parallel_group:1 deps:t1 agent_profile:worker_default -->",
            "- [ ] Exec B <!-- id:t3 phase:phase-1 lane:parallel parallel_group:1 deps:t1 agent_profile:worker_default -->",
            "- [ ] Exec C <!-- id:t4 phase:phase-1 lane:parallel parallel_group:2 deps:t2,t3 agent_profile:worker_default -->",
            "- [ ] Verify <!-- id:t5 phase:phase-1 lane:seq_post deps:t4 agent_profile:worker_default -->",
        ]
    )
    runtime, issues = compile_execution_structure(plan, tasks, task_parse_issues=parse_issues)
    assert issues == []
    assert runtime is not None
    assert runtime.parallel_limit == 3

    levels = runtime.node_levels
    assert levels["phase-1::seq_pre::t1"] < levels["phase-1::parallel::t2"]
    assert levels["phase-1::seq_pre::t1"] < levels["phase-1::parallel::t3"]
    assert levels["phase-1::parallel::t2"] < levels["phase-1::parallel::t4"]
    assert levels["phase-1::parallel::t3"] < levels["phase-1::parallel::t4"]
    assert levels["phase-1::parallel::t4"] < levels["phase-1::seq_post::t5"]
