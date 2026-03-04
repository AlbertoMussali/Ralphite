from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

import yaml


def _slug(value: str, fallback: str = "plan") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return normalized or fallback


def versioned_filename(plan_id: str, hint: str | None = None) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{_slug(hint or plan_id)}.{ts}.yaml"


def _default_behaviors() -> list[dict[str, Any]]:
    return [
        {
            "id": "prepare_dispatch_default",
            "kind": "prepare_dispatch",
            "agent": "orchestrator_default",
            "prompt_template": "Prepare the next dispatch cell and summarize prerequisites.",
            "enabled": True,
        },
        {
            "id": "merge_and_conflict_resolution_default",
            "kind": "merge_and_conflict_resolution",
            "agent": "orchestrator_default",
            "prompt_template": "Merge outputs, resolve conflicts safely, and report unresolved risks.",
            "enabled": True,
        },
        {
            "id": "summarize_work_default",
            "kind": "summarize_work",
            "agent": "orchestrator_default",
            "prompt_template": "Summarize work completed, validation status, and next handoff context.",
            "enabled": True,
        },
    ]


def make_starter_plan(goal: str | None = None) -> dict:
    plan_task = "Decompose the objective into executable steps."
    execute_task = "Implement the planned tasks and update project artifacts."
    verify_task = "Validate outcomes and summarize decisions."
    if goal:
        plan_task = f"Decompose objective: {goal}"
        execute_task = f"Execute objective: {goal}"
        verify_task = f"Verify objective outcome: {goal}"

    return {
        "version": 5,
        "plan_id": "starter_loop",
        "name": "Starter Loop",
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
        },
        "agents": [
            {
                "id": "worker_default",
                "role": "worker",
                "provider": "openai",
                "model": "gpt-4.1",
                "system_prompt": "Execute assigned task slices in isolated worker context.",
                "tools_allow": ["tool:*", "mcp:*"],
            },
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "system_prompt": "Orchestrate merges, conflict handling, and handoffs between task cells.",
                "tools_allow": ["tool:*", "mcp:*"],
            },
        ],
        "tasks": [
            {
                "id": "task_plan",
                "title": plan_task,
                "completed": False,
                "routing": {"cell": "seq_pre", "tags": ["planning"]},
                "acceptance": {"commands": [], "required_artifacts": [], "rubric": ["Task is decomposed into clear steps."]},
            },
            {
                "id": "task_execute",
                "title": execute_task,
                "completed": False,
                "deps": ["task_plan"],
                "parallel_group": 1,
                "routing": {"cell": "par_core", "tags": ["implementation"]},
                "acceptance": {"commands": [], "required_artifacts": [], "rubric": ["Feature implementation is complete."]},
            },
            {
                "id": "task_verify",
                "title": verify_task,
                "completed": False,
                "deps": ["task_execute"],
                "routing": {"cell": "seq_post", "tags": ["verification"]},
                "acceptance": {"commands": [], "required_artifacts": [], "rubric": ["Validation and summary are complete."]},
            },
        ],
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "behaviors": _default_behaviors(),
            "branched": {"lanes": ["lane_a", "lane_b"]},
            "blue_red": {"loop_unit": "per_task"},
            "custom": {"cells": []},
        },
        "outputs": {
            "required_artifacts": [
                {"id": "final_report", "format": "markdown"},
                {"id": "machine_bundle", "format": "json"},
            ]
        },
    }


def migrate_v4_to_v5(plan_data: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(plan_data)
    migrated["version"] = 5

    agents = migrated.get("agents")
    if not isinstance(agents, list):
        agents = []
    normalized_agents: list[dict[str, Any]] = []
    has_worker = False
    has_orchestrator = False
    pre_agent = None
    post_agent = None
    run_cfg = migrated.get("run") if isinstance(migrated.get("run"), dict) else {}
    if isinstance(run_cfg.get("pre_orchestrator"), dict):
        pre_agent = run_cfg["pre_orchestrator"].get("agent")
    if isinstance(run_cfg.get("post_orchestrator"), dict):
        post_agent = run_cfg["post_orchestrator"].get("agent")

    for raw in agents:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        role = str(row.get("role") or "")
        if role == "worker":
            has_worker = True
            normalized_agents.append({**row, "role": "worker"})
            continue
        if role in {"orchestrator_pre", "orchestrator_post", "orchestrator"}:
            has_orchestrator = True
            normalized_agents.append({**row, "role": "orchestrator"})
            continue
        normalized_agents.append(row)

    if not has_worker:
        normalized_agents.append(
            {
                "id": "worker_default",
                "role": "worker",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "tools_allow": ["tool:*"],
            }
        )
    if not has_orchestrator:
        normalized_agents.append(
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "tools_allow": ["tool:*"],
            }
        )
    migrated["agents"] = normalized_agents
    if "run" in migrated:
        del migrated["run"]

    tasks = migrated.get("tasks")
    if isinstance(tasks, list):
        rewritten_tasks: list[dict[str, Any]] = []
        first_parallel_index = next(
            (idx for idx, row in enumerate(tasks) if isinstance(row, dict) and int(row.get("parallel_group", 0) or 0) > 0),
            -1,
        )
        for idx, row in enumerate(tasks):
            if not isinstance(row, dict):
                continue
            copied = dict(row)
            routing = copied.get("routing")
            if not isinstance(routing, dict):
                routing = {}
            if "cell" not in routing:
                if int(copied.get("parallel_group", 0) or 0) > 0:
                    routing["cell"] = "par_core"
                elif first_parallel_index < 0 or idx < first_parallel_index:
                    routing["cell"] = "seq_pre"
                else:
                    routing["cell"] = "seq_post"
            routing.setdefault("tags", [])
            copied["routing"] = routing
            acceptance = copied.get("acceptance")
            if not isinstance(acceptance, dict):
                acceptance = {"commands": [], "required_artifacts": [], "rubric": []}
            acceptance.setdefault("commands", [])
            acceptance.setdefault("required_artifacts", [])
            acceptance.setdefault("rubric", [])
            copied["acceptance"] = acceptance
            rewritten_tasks.append(copied)
        migrated["tasks"] = rewritten_tasks

    fallback_orchestrator = (
        str(post_agent).strip()
        if isinstance(post_agent, str) and post_agent.strip()
        else str(pre_agent).strip()
        if isinstance(pre_agent, str) and pre_agent.strip()
        else "orchestrator_default"
    )
    behaviors = _default_behaviors()
    for row in behaviors:
        row["agent"] = fallback_orchestrator
    migrated["orchestration"] = {
        "template": "general_sps",
        "inference_mode": "mixed",
        "behaviors": behaviors,
        "branched": {"lanes": ["lane_a", "lane_b"]},
        "blue_red": {"loop_unit": "per_task"},
        "custom": {"cells": []},
    }
    return migrated


def make_goal_plan(goal: str) -> dict:
    plan = make_starter_plan(goal)
    plan["plan_id"] = _slug(goal[:50], "goal-plan")
    plan["name"] = f"Goal Plan: {goal[:48]}"
    return plan


def dump_yaml(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)


def _is_v5_plan_file(path: Path) -> bool:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    try:
        return int(raw.get("version", 1)) == 5
    except (TypeError, ValueError):
        return False


def _starter_target_path(plans_dir: Path) -> Path:
    default = plans_dir / "starter_loop.yaml"
    if not default.exists():
        return default
    return plans_dir / "starter_loop.v5.yaml"


def seed_starter_if_missing(plans_dir: Path) -> Path | None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in plans_dir.iterdir() if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}]

    if existing and any(_is_v5_plan_file(path) for path in existing):
        return None

    path = _starter_target_path(plans_dir)
    path.write_text(dump_yaml(make_starter_plan()), encoding="utf-8")
    return path
