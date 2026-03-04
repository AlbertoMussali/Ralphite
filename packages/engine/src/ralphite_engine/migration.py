from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ralphite_engine.templates import versioned_filename
from ralphite_engine.validation import validate_plan_content


@dataclass
class MigrationResult:
    source: Path
    destination: Path | None
    changed: bool
    warnings: list[str]


def _normalize_plan(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], bool]:
    warnings: list[str] = []
    changed = False

    graph = data.setdefault("graph", {})
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    loops = graph.setdefault("loops", [])

    supported = {"agent", "gate"}
    kept_ids: set[str] = set()
    normalized_nodes: list[dict[str, Any]] = []
    for node in nodes:
        kind = node.get("kind")
        if kind not in supported:
            warnings.append(f"dropped unsupported node kind '{kind}' ({node.get('id')})")
            changed = True
            continue
        kept_ids.add(str(node.get("id")))
        normalized_nodes.append(node)

    if len(normalized_nodes) != len(nodes):
        graph["nodes"] = normalized_nodes

    normalized_edges: list[dict[str, Any]] = []
    for edge in edges:
        src = str(edge.get("from", ""))
        dst = str(edge.get("to", ""))
        if src not in kept_ids or dst not in kept_ids:
            warnings.append(f"removed dangling edge {src}->{dst}")
            changed = True
            continue
        if edge.get("when") == "retry" and not edge.get("loop_id"):
            edge["loop_id"] = "main_loop"
            warnings.append(f"attached missing loop_id to retry edge {src}->{dst}")
            changed = True
        normalized_edges.append(edge)
    graph["edges"] = normalized_edges

    if any(edge.get("when") == "retry" for edge in normalized_edges):
        if "main_loop" not in {loop.get("id") for loop in loops if isinstance(loop, dict)}:
            loops.append({"id": "main_loop", "max_iterations": 3})
            warnings.append("added missing main_loop declaration")
            changed = True

    data.setdefault("agents", [])
    if not data["agents"]:
        data["agents"].append(
            {
                "id": "worker",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "system_prompt": "Execute assigned tasks.",
                "tools_allow": ["tool:*", "mcp:*"],
            }
        )
        changed = True
        warnings.append("added default worker agent")

    for node in graph["nodes"]:
        if node.get("kind") == "agent" and not node.get("agent_id"):
            node["agent_id"] = data["agents"][0]["id"]
            warnings.append(f"assigned missing agent_id on node {node.get('id')}")
            changed = True
        if node.get("kind") == "gate" and not node.get("gate"):
            node["gate"] = {"mode": "rubric", "pass_if": "all_acceptance_checks_pass"}
            warnings.append(f"added gate defaults on node {node.get('id')}")
            changed = True

    return data, warnings, changed


def migrate_plan_file(path: Path, out_dir: Path) -> MigrationResult:
    source = path.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return MigrationResult(source=source, destination=None, changed=False, warnings=["plan root is not a mapping"])

    normalized, warnings, changed = _normalize_plan(raw)
    content = yaml.safe_dump(normalized, sort_keys=False, allow_unicode=False)
    valid, issues, _summary = validate_plan_content(content)
    if not valid:
        warnings.extend([f"post-migration issue: {issue.get('code')} {issue.get('message')}" for issue in issues])

    if not changed:
        return MigrationResult(source=source, destination=None, changed=False, warnings=warnings)

    plan_id = str(normalized.get("plan_id") or source.stem)
    destination = out_dir / versioned_filename(plan_id, f"migrated-{source.stem}")
    destination.write_text(content, encoding="utf-8")
    return MigrationResult(source=source, destination=destination, changed=True, warnings=warnings)
