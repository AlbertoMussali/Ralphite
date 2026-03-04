from __future__ import annotations

import argparse
import os
import socket
import time
import uuid
from typing import Any

from ralphite_runner.client import APIClient
from ralphite_runner.discovery import (
    ensure_starter_plan_if_empty,
    discover_mcp_servers,
    discover_plan_files,
    discover_provider_caps,
    discover_tools,
)
from ralphite_runner.executor import execute_node
from ralphite_runner.state import RunnerStateStore


def build_capabilities(runner_id: str, workspace_root: str, seeded_starter: bool = False) -> dict[str, Any]:
    return {
        "runner_id": runner_id,
        "runner_version": "0.1.0",
        "workspace_root": workspace_root,
        "seeded_starter": seeded_starter,
        "tools": discover_tools(),
        "mcp_servers": discover_mcp_servers(),
        "provider_caps": discover_provider_caps(),
        "plan_files": discover_plan_files(workspace_root),
    }


def register_or_resume(client: APIClient, state_store: RunnerStateStore, workspace_root: str) -> tuple[str, str]:
    state = state_store.load()
    runner_id = state.get("runner_id")
    if not runner_id:
        host = socket.gethostname().split(".")[0]
        runner_id = f"{host}-{uuid.uuid4().hex[:8]}"

    seeded_starter = ensure_starter_plan_if_empty(workspace_root)
    if seeded_starter:
        print("seeded starter plan at .ralphite/plans/starter_loop.yaml")
    capabilities = build_capabilities(runner_id, workspace_root, seeded_starter=seeded_starter)
    response = client.post("/api/v1/runner/register", capabilities)
    token = response["token"]

    state_store.save({"runner_id": runner_id, "token": token})
    return runner_id, token


def run_loop(api_base: str, workspace_root: str, interval_seconds: float) -> None:
    state_store = RunnerStateStore(workspace_root)

    bootstrap_client = APIClient(api_base)
    runner_id, token = register_or_resume(bootstrap_client, state_store, workspace_root)

    client = APIClient(api_base, runner_token=token)

    while True:
        seeded_starter = ensure_starter_plan_if_empty(workspace_root)
        if seeded_starter:
            print("seeded starter plan at .ralphite/plans/starter_loop.yaml")
        capabilities = build_capabilities(runner_id, workspace_root, seeded_starter=seeded_starter)
        try:
            client.post("/api/v1/runner/heartbeat", capabilities)
        except Exception as exc:  # noqa: BLE001
            print(f"heartbeat failed: {exc}")
            time.sleep(interval_seconds)
            continue

        try:
            claimed = client.post("/api/v1/runner/claim-next", {"runner_id": runner_id})
        except Exception as exc:  # noqa: BLE001
            print(f"claim-next failed: {exc}")
            time.sleep(interval_seconds)
            continue

        if not claimed:
            time.sleep(interval_seconds)
            continue

        result, events = execute_node(claimed)

        if events:
            try:
                client.post(
                    f"/api/v1/runner/runs/{claimed['run_id']}/events/batch",
                    {"runner_id": runner_id, "events": events},
                )
            except Exception as exc:  # noqa: BLE001
                print(f"events batch failed: {exc}")

        if result["outcome"] == "success":
            payload = {
                "runner_id": runner_id,
                "node_record_id": claimed["node_record_id"],
                "result": result.get("result", {}),
                "outcome": "success",
                "decision": result.get("decision"),
            }
            try:
                client.post(f"/api/v1/runner/runs/{claimed['run_id']}/complete", payload)
            except Exception as exc:  # noqa: BLE001
                print(f"complete failed: {exc}")
        else:
            payload = {
                "runner_id": runner_id,
                "node_record_id": claimed["node_record_id"],
                "reason": result.get("result", {}).get("reason", "execution_failed"),
                "details": result.get("result", {}),
            }
            try:
                client.post(f"/api/v1/runner/runs/{claimed['run_id']}/fail", payload)
            except Exception as exc:  # noqa: BLE001
                print(f"fail endpoint failed: {exc}")

        time.sleep(float(os.getenv("RALPHITE_RUNNER_POST_TASK_SLEEP_SECONDS", "0.2")))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ralphite local runner")
    parser.add_argument("--api-base", default=os.getenv("RALPHITE_API_BASE", "http://localhost:8000"))
    parser.add_argument("--workspace-root", default=os.getenv("RALPHITE_WORKSPACE_ROOT"), required=False)
    parser.add_argument("--interval-seconds", type=float, default=float(os.getenv("RALPHITE_RUNNER_INTERVAL_SECONDS", "2")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace_root = args.workspace_root
    if not workspace_root:
        raise SystemExit("--workspace-root (or RALPHITE_WORKSPACE_ROOT) is required")

    run_loop(args.api_base, workspace_root, args.interval_seconds)


if __name__ == "__main__":
    main()
