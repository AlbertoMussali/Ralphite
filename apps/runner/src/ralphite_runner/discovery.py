from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import yaml


def discover_tools() -> list[str]:
    base_candidates = ["python", "python3", "node", "npm", "pnpm", "git", "rg", "docker", "make"]
    found = [tool for tool in base_candidates if shutil.which(tool)]

    extras = os.getenv("RALPHITE_EXTRA_TOOLS", "").strip()
    if extras:
        for tool in [item.strip() for item in extras.split(",") if item.strip()]:
            if tool not in found:
                found.append(tool)

    return sorted(found)


def discover_mcp_servers() -> list[dict]:
    raw = os.getenv("RALPHITE_MCP_SERVERS_JSON", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict) and "id" in item]
    return []


def discover_provider_caps() -> list[dict]:
    return [{"provider": "openai", "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"]}]


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


def ensure_starter_plan_if_empty(workspace_root: str) -> bool:
    plan_dir = Path(workspace_root) / ".ralphite" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)

    existing = [
        path
        for path in plan_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    ]
    if existing:
        return False

    starter_path = plan_dir / "starter_loop.yaml"
    content = yaml.safe_dump(make_starter_plan(), sort_keys=False, allow_unicode=False)
    starter_path.write_text(content, encoding="utf-8")
    return True


def discover_plan_files(workspace_root: str) -> list[dict]:
    plan_dir = Path(workspace_root) / ".ralphite" / "plans"
    if not plan_dir.exists():
        return []

    rows: list[dict] = []
    for path in sorted(plan_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".yaml", ".yml"}:
            continue

        content = path.read_bytes()
        text_content = content.decode("utf-8", errors="replace")
        stat = path.stat()
        rows.append(
            {
                "path": str(path.relative_to(workspace_root)).replace("\\", "/"),
                "checksum_sha256": sha256(content).hexdigest(),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "content": text_content,
            }
        )

    return rows
