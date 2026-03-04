from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re

import yaml


def _slug(value: str, fallback: str = "plan") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return normalized or fallback


def versioned_filename(plan_id: str, hint: str | None = None) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{_slug(hint or plan_id)}.{ts}.yaml"


def make_starter_plan(goal: str | None = None) -> dict:
    plan_task = "Decompose the objective into executable steps."
    execute_task = "Implement the planned tasks and update project artifacts."
    verify_task = "Validate outcomes and summarize decisions."
    if goal:
        plan_task = f"Decompose objective: {goal}"
        execute_task = f"Execute objective: {goal}"
        verify_task = f"Verify objective outcome: {goal}"

    return {
        "version": 4,
        "plan_id": "starter_block",
        "name": "Starter Block",
        "materials": {
            "autodiscover": {
                "enabled": True,
                "path": ".ralphite/plans",
                "include_globs": ["**/*.yaml", "**/*.yml", "**/*.md", "**/*.txt"],
            },
            "includes": [],
            "uploads": [],
        },
        "run": {
            "pre_orchestrator": {"enabled": False, "agent": "orchestrator_pre_default"},
            "post_orchestrator": {"enabled": True, "agent": "orchestrator_post_default"},
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
                "id": "orchestrator_pre_default",
                "role": "orchestrator_pre",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "system_prompt": (
                    "You are the pre-orchestrator. Validate order/dependencies, workspace readiness, "
                    "and how workers should execute by source task order and parallel groups."
                ),
                "tools_allow": ["tool:*", "mcp:*"],
            },
            {
                "id": "orchestrator_post_default",
                "role": "orchestrator_post",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "system_prompt": (
                    "You are the post-orchestrator. Ensure worker outputs are integrated back to main while "
                    "preserving worker commit intent, clean temporary artifacts/worktrees, and produce a concise summary."
                ),
                "tools_allow": ["tool:*", "mcp:*"],
            },
        ],
        "tasks": [
            {"id": "task_plan", "title": plan_task, "completed": False},
            {"id": "task_execute", "title": execute_task, "completed": False, "deps": ["task_plan"], "parallel_group": 1},
            {"id": "task_verify", "title": verify_task, "completed": False, "deps": ["task_execute"]},
        ],
        "outputs": {
            "required_artifacts": [
                {"id": "final_report", "format": "markdown"},
                {"id": "machine_bundle", "format": "json"},
            ]
        },
    }


def make_goal_plan(goal: str) -> dict:
    plan = make_starter_plan(goal)
    plan["plan_id"] = _slug(goal[:50], "goal-plan")
    plan["name"] = f"Goal Plan: {goal[:48]}"
    return plan


def dump_yaml(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)


def _is_v4_plan_file(path: Path) -> bool:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    try:
        return int(raw.get("version", 1)) == 4
    except (TypeError, ValueError):
        return False


def _starter_target_path(plans_dir: Path) -> Path:
    default = plans_dir / "starter_block.yaml"
    if not default.exists():
        return default
    return plans_dir / "starter_block.v4.yaml"


def seed_starter_if_missing(plans_dir: Path) -> Path | None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in plans_dir.iterdir() if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}]

    if existing and any(_is_v4_plan_file(path) for path in existing):
        return None

    path = _starter_target_path(plans_dir)
    path.write_text(dump_yaml(make_starter_plan()), encoding="utf-8")
    return path
