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


def make_starter_plan() -> dict:
    return {
        "version": 1,
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
        "agents": [
            {
                "id": "planner",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "system_prompt": "Decompose and prioritize tasks.",
                "tools_allow": ["tool:*", "mcp:*"],
            },
            {
                "id": "worker",
                "provider": "openai",
                "model": "gpt-4.1",
                "system_prompt": "Execute assigned tasks and produce artifacts.",
                "tools_allow": ["tool:*", "mcp:*"],
            },
        ],
        "graph": {
            "nodes": [
                {
                    "id": "n1_plan",
                    "kind": "agent",
                    "agent_id": "planner",
                    "task": "Break objective into actionable tasks.",
                    "group": "planning",
                    "depends_on": [],
                },
                {
                    "id": "n2_execute",
                    "kind": "agent",
                    "agent_id": "worker",
                    "task": "Execute and update project artifacts.",
                    "group": "execution",
                    "depends_on": ["n1_plan"],
                },
                {
                    "id": "n3_gate",
                    "kind": "gate",
                    "gate": {"mode": "rubric", "pass_if": "all_acceptance_checks_pass"},
                    "group": "quality",
                    "depends_on": ["n2_execute"],
                },
            ],
            "edges": [
                {"from": "n1_plan", "to": "n2_execute", "when": "success"},
                {"from": "n2_execute", "to": "n3_gate", "when": "success"},
                {"from": "n3_gate", "to": "n2_execute", "when": "retry", "loop_id": "main_loop"},
            ],
            "loops": [{"id": "main_loop", "max_iterations": 3}],
        },
        "constraints": {
            "max_runtime_seconds": 3600,
            "max_total_steps": 120,
            "max_cost_usd": 10.0,
            "fail_fast": True,
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
    nodes = plan["graph"]["nodes"]
    nodes[0]["task"] = f"Create an execution plan for goal: {goal}"
    nodes[1]["task"] = f"Execute the plan for goal: {goal}"
    return plan


def dump_yaml(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)


def seed_starter_if_missing(plans_dir: Path) -> Path | None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in plans_dir.iterdir() if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}]
    if existing:
        return None
    path = plans_dir / "starter_loop.yaml"
    path.write_text(dump_yaml(make_starter_plan()), encoding="utf-8")
    return path
