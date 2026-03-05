from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

import yaml

from ralphite.engine.bundled_templates import (
    STARTER_BUGFIX,
    STARTER_DOCS_UPDATE,
    STARTER_REFACTOR,
    STARTER_RELEASE_PREP,
)


def _slug(value: str, fallback: str = "plan") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return normalized or fallback


def versioned_filename(plan_id: str, hint: str | None = None) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{_slug(hint or plan_id)}.{ts}.yaml"


def _legacy_default_behaviors() -> list[dict[str, Any]]:
    return [
        {
            "id": "prepare_dispatch_default",
            "kind": "prepare_dispatch",
            "agent": "orchestrator_default",
            "prompt_template": (
                "Prepare the next dispatch cell and summarize prerequisites."
            ),
            "enabled": True,
        },
        {
            "id": "merge_and_conflict_resolution_default",
            "kind": "merge_and_conflict_resolution",
            "agent": "orchestrator_default",
            "prompt_template": (
                "Merge outputs, resolve conflicts safely, and report unresolved risks."
            ),
            "enabled": True,
        },
        {
            "id": "summarize_work_default",
            "kind": "summarize_work",
            "agent": "orchestrator_default",
            "prompt_template": (
                "Summarize work completed, validation status, and next handoff context."
            ),
            "enabled": True,
        },
    ]


def _legacy_default_agents() -> list[dict[str, Any]]:
    return [
        {
            "id": "worker_default",
            "role": "worker",
            "provider": "codex",
            "model": "gpt-5.3-codex",
            "reasoning_effort": "medium",
            "system_prompt": "Execute assigned task slices in isolated worker context.",
            "tools_allow": ["tool:*", "mcp:*"],
        },
        {
            "id": "orchestrator_default",
            "role": "orchestrator",
            "provider": "codex",
            "model": "gpt-5.3-codex",
            "reasoning_effort": "medium",
            "system_prompt": (
                "Orchestrate merges, conflict handling, and handoffs between task cells."
            ),
            "tools_allow": ["tool:*", "mcp:*"],
        },
    ]


def _legacy_plan_shell(
    *,
    template: str,
    plan_id: str,
    name: str,
    lanes: list[str],
    loop_unit: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "plan_id": plan_id,
        "name": name,
        "materials": {
            "autodiscover": {
                "enabled": True,
                "path": ".ralphite/plans",
                "include_globs": ["**/*.yaml", "**/*.yml", "**/*.md", "**/*.txt"],
            },
            "includes": [],
            "uploads": [],
        },
        "constraints": {
            "max_runtime_seconds": 3600,
            "max_total_steps": 120,
            "max_cost_usd": 10.0,
            "fail_fast": True,
            "max_parallel": 3,
            "acceptance_timeout_seconds": 120,
            "max_retries_per_node": 0,
        },
        "agents": _legacy_default_agents(),
        "tasks": [],
        "orchestration": {
            "template": template,
            "inference_mode": "mixed",
            "behaviors": _legacy_default_behaviors(),
            "branched": {"lanes": list(lanes)},
            "blue_red": {"loop_unit": loop_unit},
            "custom": {"cells": []},
        },
        "outputs": {
            "required_artifacts": [
                {"id": "final_report", "format": "markdown"},
                {"id": "machine_bundle", "format": "json"},
            ]
        },
    }


def _legacy_goal_titles(goal: str | None) -> tuple[str, str, str]:
    plan_task = "Decompose the objective into executable steps."
    execute_task = "Implement the planned tasks and update project artifacts."
    verify_task = "Validate outcomes and summarize decisions."
    if goal:
        plan_task = f"Decompose objective: {goal}"
        execute_task = f"Execute objective: {goal}"
        verify_task = f"Verify objective outcome: {goal}"
    return plan_task, execute_task, verify_task


def _legacy_bootstrap_plan(
    *,
    template: str,
    plan_id: str,
    name: str,
    goal: str | None,
    branched_lanes: list[str] | None,
    blue_red_loop_unit: str,
) -> dict[str, Any]:
    lanes = [
        item.strip()
        for item in (branched_lanes or ["lane_a", "lane_b"])
        if item and item.strip()
    ]
    if not lanes:
        lanes = ["lane_a", "lane_b"]
    plan_task, execute_task, verify_task = _legacy_goal_titles(goal)
    shell = _legacy_plan_shell(
        template=template,
        plan_id=plan_id,
        name=name,
        lanes=lanes,
        loop_unit=blue_red_loop_unit,
    )

    if template == "general_sps":
        shell["tasks"] = [
            {
                "id": "task_plan",
                "title": plan_task,
                "completed": False,
                "routing": {"cell": "seq_pre", "tags": ["planning"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Task is decomposed into clear steps."],
                },
            },
            {
                "id": "task_execute",
                "title": execute_task,
                "completed": False,
                "deps": ["task_plan"],
                "routing": {"cell": "par_core", "tags": ["implementation"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Feature implementation is complete."],
                },
            },
            {
                "id": "task_verify",
                "title": verify_task,
                "completed": False,
                "deps": ["task_execute"],
                "routing": {"cell": "seq_post", "tags": ["verification"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Validation and summary are complete."],
                },
            },
        ]
    elif template == "branched":
        lane_a = lanes[0]
        lane_b = lanes[1] if len(lanes) > 1 else lanes[0]
        shell["tasks"] = [
            {
                "id": "task_trunk_prelude",
                "title": plan_task,
                "completed": False,
                "routing": {"group": "trunk", "tags": ["trunk", "planning"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Prelude context is clear."],
                },
            },
            {
                "id": "task_lane_a",
                "title": f"Lane work: {lane_a}",
                "completed": False,
                "deps": ["task_trunk_prelude"],
                "routing": {"lane": lane_a, "tags": ["lane"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Lane A work is complete."],
                },
            },
            {
                "id": "task_lane_b",
                "title": f"Lane work: {lane_b}",
                "completed": False,
                "deps": ["task_trunk_prelude"],
                "routing": {"lane": lane_b, "tags": ["lane"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Lane B work is complete."],
                },
            },
            {
                "id": "task_trunk_finalize",
                "title": verify_task,
                "completed": False,
                "deps": ["task_lane_a", "task_lane_b"],
                "routing": {
                    "group": "trunk",
                    "cell": "trunk_post",
                    "tags": ["trunk", "finalize"],
                },
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Joined lane output is coherent."],
                },
            },
        ]
    elif template == "blue_red":
        shell["tasks"] = [
            {
                "id": "task_feature_1",
                "title": execute_task,
                "completed": False,
                "routing": {
                    "cell": "cycle",
                    "team_mode": "blue_red",
                    "tags": ["feature"],
                },
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Feature pass is implemented and reviewed."],
                },
            },
            {
                "id": "task_feature_2",
                "title": verify_task,
                "completed": False,
                "routing": {
                    "cell": "cycle",
                    "team_mode": "blue_red",
                    "tags": ["feature"],
                },
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Review pass confirms acceptable quality."],
                },
            },
        ]
    elif template == "custom":
        shell["orchestration"]["custom"] = {
            "cells": [
                {"id": "pre", "kind": "sequential", "task_ids": ["task_pre"]},
                {
                    "id": "merge",
                    "kind": "orchestrator",
                    "behavior": "merge_and_conflict_resolution_default",
                    "depends_on": ["pre"],
                },
                {
                    "id": "post",
                    "kind": "sequential",
                    "task_ids": ["task_post"],
                    "depends_on": ["merge"],
                },
            ]
        }
        shell["tasks"] = [
            {
                "id": "task_pre",
                "title": plan_task,
                "completed": False,
                "routing": {"cell": "pre", "tags": ["custom"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Pre-step completed."],
                },
            },
            {
                "id": "task_post",
                "title": verify_task,
                "completed": False,
                "routing": {"cell": "post", "tags": ["custom"]},
                "acceptance": {
                    "commands": [],
                    "required_artifacts": [],
                    "rubric": ["Post-step completed."],
                },
            },
        ]
    else:
        raise ValueError(f"unsupported template: {template}")

    return shell


def make_bootstrap_plan(
    *,
    template: str = "starter_bugfix",
    plan_id: str | None = None,
    name: str | None = None,
    goal: str | None = None,
    branched_lanes: list[str] | None = None,
    blue_red_loop_unit: str = "per_task",
) -> dict[str, Any]:
    starter_templates = {
        "starter_bugfix": STARTER_BUGFIX,
        "starter_refactor": STARTER_REFACTOR,
        "starter_docs_update": STARTER_DOCS_UPDATE,
        "starter_release_prep": STARTER_RELEASE_PREP,
    }
    legacy_templates = {"general_sps", "branched", "blue_red", "custom"}

    if template in starter_templates:
        plan = yaml.safe_load(starter_templates[template])
        if plan_id and plan_id != "starter_loop":
            plan["plan_id"] = plan_id
        if name and name != "Starter Loop":
            plan["name"] = name
        if goal:
            plan["tasks"] = [
                {
                    "id": "task_plan",
                    "title": f"Decompose objective: {goal}",
                    "completed": False,
                    "routing": {"cell": "seq_pre", "tags": ["planning"]},
                    "acceptance": {
                        "commands": [],
                        "required_artifacts": [],
                        "rubric": [f"Execution plan created for goal: {goal}"],
                    },
                },
                {
                    "id": "task_execute",
                    "title": f"Execute objective: {goal}",
                    "completed": False,
                    "deps": ["task_plan"],
                    "routing": {"cell": "par_core", "tags": ["implementation"]},
                    "acceptance": {
                        "commands": [],
                        "required_artifacts": [],
                        "rubric": ["Feature implementation is complete."],
                    },
                },
                {
                    "id": "task_verify",
                    "title": f"Verify objective outcome: {goal}",
                    "completed": False,
                    "deps": ["task_execute"],
                    "routing": {"cell": "seq_post", "tags": ["verification"]},
                    "acceptance": {
                        "commands": [],
                        "required_artifacts": [],
                        "rubric": ["Validation and summary are complete."],
                    },
                },
            ]
            plan["orchestration"]["template"] = "general_sps"
        if branched_lanes and plan["orchestration"]["template"] == "branched":
            plan["orchestration"]["branched"]["lanes"] = branched_lanes
        if blue_red_loop_unit and plan["orchestration"]["template"] == "blue_red":
            plan["orchestration"]["blue_red"]["loop_unit"] = blue_red_loop_unit
        return plan

    if template in legacy_templates:
        return _legacy_bootstrap_plan(
            template=template,
            plan_id=plan_id or "starter_loop",
            name=name or "Starter Loop",
            goal=goal,
            branched_lanes=branched_lanes,
            blue_red_loop_unit=blue_red_loop_unit,
        )

    raise ValueError(f"unsupported template: {template}")


def make_starter_plan(goal: str | None = None) -> dict[str, Any]:
    return make_bootstrap_plan(goal=goal)


def make_goal_plan(goal: str) -> dict[str, Any]:
    return make_bootstrap_plan(
        template="starter_bugfix",
        goal=goal,
        plan_id=_slug(goal[:50], "goal-plan") if goal else "goal-plan",
        name=f"Goal Plan: {goal[:48]}" if goal else "Goal Plan",
    )


def dump_yaml(plan: dict[str, Any]) -> str:
    return yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)


def _is_v1_plan_file(path: Path) -> bool:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    try:
        return int(raw.get("version", 0)) == 1
    except (TypeError, ValueError):
        return False


def _starter_target_path(plans_dir: Path) -> Path:
    default = plans_dir / "starter_bugfix.yaml"
    if not default.exists():
        return default
    return plans_dir / "starter_bugfix.bootstrap.yaml"


def seed_starter_if_missing(plans_dir: Path) -> Path | None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        p
        for p in plans_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}
    ]

    if existing and any(_is_v1_plan_file(path) for path in existing):
        return None

    path = _starter_target_path(plans_dir)
    path.write_text(dump_yaml(make_starter_plan(goal=None)), encoding="utf-8")
    return path
