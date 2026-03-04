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


def make_starter_task_markdown(goal: str | None = None) -> str:
    plan_task = "Decompose the objective into executable steps."
    execute_task = "Implement the planned tasks and update project artifacts."
    verify_task = "Validate outcomes and summarize decisions."
    if goal:
        plan_task = f"Decompose objective: {goal}"
        execute_task = f"Execute objective: {goal}"
        verify_task = f"Verify objective outcome: {goal}"

    return "\n".join(
        [
            "# Ralphite Tasks",
            "",
            f"- [ ] {plan_task} <!-- id:task_plan phase:phase-1 lane:seq_pre agent_profile:worker_default -->",
            f"- [ ] {execute_task} <!-- id:task_execute phase:phase-1 lane:parallel deps:task_plan agent_profile:worker_default -->",
            f"- [ ] {verify_task} <!-- id:task_verify phase:phase-1 lane:seq_post deps:task_execute agent_profile:worker_default -->",
            "",
        ]
    )


def make_starter_plan() -> dict:
    return {
        "version": 3,
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
        "task_source": {
            "kind": "markdown_checklist",
            "path": "RALPHEX_TASK.md",
            "parser_version": 3,
        },
        "agent_profiles": [
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
                    "and how workers should execute in seq_pre -> parallel -> seq_post order."
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
        "execution_structure": {
            "phases": [
                {
                    "id": "phase-1",
                    "label": "Default Phase",
                    "pre_orchestrator": {
                        "enabled": False,
                        "agent_profile_id": "orchestrator_pre_default",
                    },
                    "post_orchestrator": {
                        "enabled": True,
                        "agent_profile_id": "orchestrator_post_default",
                    },
                }
            ]
        },
        "constraints": {
            "max_runtime_seconds": 3600,
            "max_total_steps": 120,
            "max_cost_usd": 10.0,
            "fail_fast": True,
            "max_parallel": 3,
        },
        "outputs": {
            "required_artifacts": [
                {"id": "final_report", "format": "markdown"},
                {"id": "machine_bundle", "format": "json"},
            ]
        },
    }


def make_goal_plan(goal: str) -> dict:
    plan = make_starter_plan()
    plan["plan_id"] = _slug(goal[:50], "goal-plan")
    plan["name"] = f"Goal Plan: {goal[:48]}"
    return plan


def dump_yaml(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)


def _is_v3_plan_file(path: Path) -> bool:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    try:
        return int(raw.get("version", 1)) == 3
    except (TypeError, ValueError):
        return False


def _starter_target_path(plans_dir: Path) -> Path:
    default = plans_dir / "starter_block.yaml"
    if not default.exists():
        return default
    return plans_dir / "starter_block.v3.yaml"


def seed_starter_if_missing(plans_dir: Path) -> Path | None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in plans_dir.iterdir() if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}]
    workspace_root = plans_dir.parent.parent
    task_file = workspace_root / "RALPHEX_TASK.md"
    if not task_file.exists():
        task_file.write_text(make_starter_task_markdown(), encoding="utf-8")

    # Keep user-provided plans, but ensure at least one v3 starter exists.
    if existing and any(_is_v3_plan_file(path) for path in existing):
        return None

    path = _starter_target_path(plans_dir)
    path.write_text(dump_yaml(make_starter_plan()), encoding="utf-8")
    return path
