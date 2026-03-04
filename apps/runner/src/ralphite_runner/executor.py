from __future__ import annotations

import os
import time
from typing import Any


def _tool_allowed(tool_id: str, snapshot: dict[str, Any]) -> bool:
    deny_tools = set(snapshot.get("deny_tools", []))
    allow_tools = set(snapshot.get("allow_tools", []))

    if tool_id in deny_tools:
        return False
    if not allow_tools:
        return True
    if "tool:*" in allow_tools:
        return True
    return tool_id in allow_tools


def _mcp_allowed(mcp_id: str, snapshot: dict[str, Any]) -> bool:
    deny_mcps = set(snapshot.get("deny_mcps", []))
    allow_mcps = set(snapshot.get("allow_mcps", []))

    if mcp_id in deny_mcps:
        return False
    if not allow_mcps:
        return True
    if "mcp:*" in allow_mcps:
        return True
    return mcp_id in allow_mcps


def execute_node(node: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = node["payload"]
    snapshot = node.get("permission_snapshot", {})

    events: list[dict[str, Any]] = [
        {
            "group": node.get("group"),
            "task_id": node.get("node_id"),
            "stage": "task",
            "event": "NODE_HEARTBEAT",
            "level": "info",
            "message": "runner started node",
            "meta": {"attempt": node.get("attempt_count", 1)},
        }
    ]

    if node["kind"] == "agent":
        requested = payload.get("agent", {}).get("tools_allow", [])
        denied: list[str] = []
        for tool in requested:
            if tool.startswith("tool:") and not _tool_allowed(tool, snapshot):
                denied.append(tool)
            if tool.startswith("mcp:") and not _mcp_allowed(tool, snapshot):
                denied.append(tool)

        if denied:
            return (
                {
                    "outcome": "failed",
                    "result": {"reason": "permission_denied", "denied": denied},
                },
                events,
            )

        task = payload.get("task") or ""
        if "[fail]" in task.lower():
            return (
                {
                    "outcome": "failed",
                    "result": {"reason": "task_marker_failure", "task": task},
                },
                events,
            )

        time.sleep(float(os.getenv("RALPHITE_RUNNER_SIMULATED_TASK_SECONDS", "0.2")))
        return (
            {
                "outcome": "success",
                "result": {
                    "summary": f"Executed task: {task[:120]}",
                    "agent_id": payload.get("agent_id"),
                    "model": payload.get("agent", {}).get("model"),
                },
            },
            events,
        )

    if node["kind"] == "gate":
        gate = payload.get("gate") or {}
        pass_if = str(gate.get("pass_if", "")).lower()

        decision = "pass"
        retry_once_enabled = os.getenv("RALPHITE_GATE_RETRY_ONCE", "1") == "1"
        if retry_once_enabled and "all_acceptance_checks_pass" in pass_if and int(node.get("attempt_count", 1)) == 1:
            decision = "retry"

        events.append(
            {
                "group": node.get("group"),
                "task_id": node.get("node_id"),
                "stage": "orchestrator",
                "event": "GATE_PASS" if decision == "pass" else "GATE_RETRY",
                "level": "info" if decision == "pass" else "warn",
                "message": f"gate decision: {decision}",
                "meta": {"pass_if": gate.get("pass_if")},
            }
        )

        return (
            {
                "outcome": "success",
                "decision": decision,
                "result": {"gate_mode": gate.get("mode"), "pass_if": gate.get("pass_if")},
            },
            events,
        )

    return (
        {
            "outcome": "failed",
            "result": {"reason": f"unknown_node_kind:{node['kind']}"},
        },
        events,
    )
