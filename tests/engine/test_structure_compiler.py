from __future__ import annotations

from copy import deepcopy

from ralphite.engine.structure_compiler import compile_execution_structure
from ralphite.engine.task_parser import parse_plan_tasks
from ralphite.schemas.plan import PlanSpec


def _base_plan() -> dict:
    return {
        "version": 1,
        "plan_id": "structure",
        "name": "structure",
        "materials": {
            "autodiscover": {"enabled": False, "path": ".", "include_globs": []},
            "includes": [],
            "uploads": [],
        },
        "constraints": {"max_parallel": 3},
        "agents": [
            {
                "id": "worker_default",
                "role": "worker",
                "provider": "codex",
                "model": "gpt-5.3-codex",
            },
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "codex",
                "model": "gpt-5.3-codex",
            },
        ],
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "behaviors": [
                {
                    "id": "prepare_default",
                    "kind": "prepare_dispatch",
                    "agent": "orchestrator_default",
                    "enabled": True,
                },
                {
                    "id": "merge_default",
                    "kind": "merge_and_conflict_resolution",
                    "agent": "orchestrator_default",
                    "enabled": True,
                },
                {
                    "id": "summarize_default",
                    "kind": "summarize_work",
                    "agent": "orchestrator_default",
                    "enabled": True,
                },
            ],
            "branched": {"lanes": ["lane_a", "lane_b"]},
            "blue_red": {"loop_unit": "per_task"},
            "custom": {"cells": []},
        },
        "tasks": [],
        "outputs": {"required_artifacts": []},
    }


def test_compile_execution_structure_enforces_block_order() -> None:
    data = _base_plan()
    data["tasks"] = [
        {
            "id": "t1",
            "title": "Prep",
            "completed": False,
            "routing": {"cell": "seq_pre"},
        },
        {
            "id": "t2",
            "title": "Exec A",
            "completed": False,
            "deps": ["t1"],
            "routing": {"cell": "par_core"},
        },
        {
            "id": "t3",
            "title": "Exec B",
            "completed": False,
            "deps": ["t1"],
            "routing": {"cell": "par_core"},
        },
        {
            "id": "t4",
            "title": "Exec C",
            "completed": False,
            "deps": ["t2", "t3"],
            "routing": {"cell": "seq_post"},
        },
        {
            "id": "t5",
            "title": "Verify",
            "completed": False,
            "deps": ["t4"],
            "routing": {"cell": "seq_post"},
        },
    ]
    plan = PlanSpec.model_validate(data)

    tasks, parse_issues = parse_plan_tasks(plan)
    runtime, issues = compile_execution_structure(
        plan, tasks, task_parse_issues=parse_issues
    )

    assert issues == []
    assert runtime is not None
    assert runtime.parallel_limit == 3

    levels = runtime.node_levels
    assert levels["phase-1::task::t1"] < levels["phase-1::task::t2"]
    assert levels["phase-1::task::t1"] < levels["phase-1::task::t3"]
    assert levels["phase-1::task::t2"] < levels["phase-1::task::t4"]
    assert levels["phase-1::task::t3"] < levels["phase-1::task::t4"]
    assert levels["phase-1::task::t4"] < levels["phase-1::task::t5"]

    assert [block.kind for block in runtime.blocks] == [
        "sequential",
        "orchestrator",
        "parallel",
        "orchestrator",
        "sequential",
        "orchestrator",
    ]


def test_compile_branched_template_builds_split_and_join_cells() -> None:
    data = _base_plan()
    data["orchestration"]["template"] = "branched"
    data["tasks"] = [
        {
            "id": "t1",
            "title": "Trunk prep",
            "completed": False,
            "routing": {"group": "trunk"},
        },
        {
            "id": "t2",
            "title": "Lane A",
            "completed": False,
            "routing": {"lane": "lane_a"},
        },
        {
            "id": "t3",
            "title": "Lane B",
            "completed": False,
            "routing": {"lane": "lane_b"},
        },
        {
            "id": "t4",
            "title": "Trunk post",
            "completed": False,
            "routing": {"cell": "trunk_post"},
        },
    ]
    plan = PlanSpec.model_validate(data)

    tasks, parse_issues = parse_plan_tasks(plan)
    runtime, issues = compile_execution_structure(
        plan, tasks, task_parse_issues=parse_issues
    )

    assert issues == []
    assert runtime is not None
    cell_ids = [cell.id for cell in runtime.resolved_cells]
    assert "split_dispatch" in cell_ids
    assert "lane_a_merge" in cell_ids
    assert "lane_b_merge" in cell_ids
    assert "join_lanes" in cell_ids
    assert runtime.task_assignment["t2"].startswith("lane_a_")
    assert runtime.task_assignment["t3"].startswith("lane_b_")


def test_compile_blue_red_template_emits_blue_and_red_passes() -> None:
    data = _base_plan()
    data["orchestration"]["template"] = "blue_red"
    data["tasks"] = [
        {
            "id": "t1",
            "title": "Feature",
            "completed": False,
            "routing": {"team_mode": "blue_red"},
        },
        {
            "id": "t2",
            "title": "Second feature",
            "completed": False,
            "routing": {"team_mode": "blue_red"},
        },
    ]
    plan = PlanSpec.model_validate(data)

    tasks, parse_issues = parse_plan_tasks(plan)
    runtime, issues = compile_execution_structure(
        plan, tasks, task_parse_issues=parse_issues
    )

    assert issues == []
    assert runtime is not None
    node_ids = [node.id for node in runtime.nodes]
    assert "phase-1::task::t1::blue" in node_ids
    assert "phase-1::task::t1::red" in node_ids
    assert "phase-1::task::t2::blue" in node_ids
    assert "phase-1::task::t2::red" in node_ids

    levels = runtime.node_levels
    assert levels["phase-1::task::t1::blue"] < levels["phase-1::task::t1::red"]


def test_compile_custom_template_uses_explicit_cells() -> None:
    data = deepcopy(_base_plan())
    data["orchestration"]["template"] = "custom"
    data["orchestration"]["custom"] = {
        "cells": [
            {"id": "pre", "kind": "sequential", "task_ids": ["t1"]},
            {
                "id": "merge",
                "kind": "orchestrator",
                "behavior": "merge_default",
                "depends_on": ["pre"],
            },
            {
                "id": "post",
                "kind": "sequential",
                "task_ids": ["t2"],
                "depends_on": ["merge"],
            },
        ]
    }
    data["tasks"] = [
        {"id": "t1", "title": "Prep", "completed": False, "routing": {"cell": "pre"}},
        {
            "id": "t2",
            "title": "Finalize",
            "completed": False,
            "routing": {"cell": "post"},
        },
    ]
    plan = PlanSpec.model_validate(data)

    tasks, parse_issues = parse_plan_tasks(plan)
    runtime, issues = compile_execution_structure(
        plan, tasks, task_parse_issues=parse_issues
    )

    assert issues == []
    assert runtime is not None
    assert [cell.id for cell in runtime.resolved_cells] == ["pre", "merge", "post"]


def test_compile_custom_template_reports_unknown_dep_cell() -> None:
    data = deepcopy(_base_plan())
    data["orchestration"]["template"] = "custom"
    data["orchestration"]["custom"] = {
        "cells": [
            {"id": "pre", "kind": "sequential", "task_ids": ["t1"]},
            {
                "id": "post",
                "kind": "sequential",
                "task_ids": ["t2"],
                "depends_on": ["missing_cell"],
            },
        ]
    }
    data["tasks"] = [
        {"id": "t1", "title": "Prep", "completed": False},
        {"id": "t2", "title": "Finalize", "completed": False},
    ]
    plan = PlanSpec.model_validate(data)

    tasks, parse_issues = parse_plan_tasks(plan)
    runtime, issues = compile_execution_structure(
        plan, tasks, task_parse_issues=parse_issues
    )

    assert runtime is not None
    assert any("depends on unknown cell" in issue for issue in issues)
