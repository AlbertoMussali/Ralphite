from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re

import yaml


def make_starter_plan_dict() -> dict:
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


def dump_plan_yaml(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)


def slugify(value: str, fallback: str = "plan") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return normalized or fallback


def versioned_filename(plan_id: str, hint: str | None = None) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    base = slugify(hint or plan_id or "plan")
    return f"{base}.{ts}.yaml"


def write_workspace_plan(workspace_root: str, filename: str, content: str) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    plans_dir = (root / ".ralphite" / "plans").resolve()
    plans_dir.mkdir(parents=True, exist_ok=True)
    target = (plans_dir / filename).resolve()
    if plans_dir not in target.parents:
        raise ValueError("target path escapes workspace .ralphite/plans")
    target.write_text(content, encoding="utf-8")
    return target


def workspace_relative_path(workspace_root: str, absolute_path: str | Path) -> str:
    root = Path(workspace_root).expanduser().resolve()
    target = Path(absolute_path).expanduser().resolve()
    relative = target.relative_to(root)
    return str(relative).replace("\\", "/")
