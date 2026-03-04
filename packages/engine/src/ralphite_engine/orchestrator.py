from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from typing import Any, Generator
from uuid import uuid4

from ralphite_engine.config import LocalConfig, ensure_workspace_layout, load_config
from ralphite_engine.models import ArtifactIndex, NodeRuntimeState, RunCheckpoint, RunPersistenceState, RunViewState
from ralphite_engine.recovery import recoverable_run_ids, to_paused_for_recovery
from ralphite_engine.run_store import RunStore
from ralphite_engine.store import HistoryStore
from ralphite_engine.taxonomy import classify_failure
from ralphite_engine.templates import make_goal_plan, seed_starter_if_missing
from ralphite_engine.validation import parse_plan_yaml, validate_plan_content
from ralphite_schemas.plan import EdgeWhen, PlanSpecV1
from ralphite_schemas.validation import compile_plan


@dataclass
class RuntimeHandle:
    run: RunViewState
    plan: PlanSpecV1
    permission_snapshot: dict[str, list[str]]
    event_queue: Queue[dict[str, Any]] = field(default_factory=Queue)
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    seq: int = 0
    loop_counts: dict[str, int] = field(default_factory=dict)
    thread: threading.Thread | None = None


class LocalOrchestrator:
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.paths = ensure_workspace_layout(self.workspace_root)
        self.config: LocalConfig = load_config(self.workspace_root)
        self.history = HistoryStore(self.paths["history"])
        self.run_store = RunStore(self.paths["runs"])
        self.active: dict[str, RuntimeHandle] = {}
        seed_starter_if_missing(self.paths["plans"])
        self._bootstrap_recovery_candidates()

    def _bootstrap_recovery_candidates(self) -> None:
        for run_id in self.run_store.list_run_ids():
            state = self.run_store.load_state(run_id)
            if not state:
                continue
            if state.status in {"running", "checkpointing"} and self.run_store.lock_is_stale(run_id):
                recovering = RunPersistenceState(
                    run_id=state.run_id,
                    status="recovering",
                    plan_path=state.plan_path,
                    run=state.run,
                    loop_counts=state.loop_counts,
                    last_seq=state.last_seq,
                )
                self.run_store.write_state(recovering)
                paused = to_paused_for_recovery(recovering, self.run_store.load_checkpoint(run_id))
                self.run_store.write_state(paused)
                self.history.upsert(paused.run)

    def list_plans(self) -> list[Path]:
        return sorted(
            [p for p in self.paths["plans"].iterdir() if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def default_permission_snapshot(self) -> dict[str, list[str]]:
        return {
            "allow_tools": list(self.config.allow_tools),
            "deny_tools": list(self.config.deny_tools),
            "allow_mcps": list(self.config.allow_mcps),
            "deny_mcps": list(self.config.deny_mcps),
        }

    def _resolve_plan_path(self, plan_ref: str | None) -> Path:
        if plan_ref:
            given = Path(plan_ref)
            candidates = [given]
            if not given.is_absolute():
                candidates.append(self.workspace_root / plan_ref)
                candidates.append(self.paths["plans"] / plan_ref)
            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    return candidate.resolve()
            raise FileNotFoundError(f"plan not found: {plan_ref}")

        if self.config.default_plan:
            try:
                default_path = self._resolve_plan_path(self.config.default_plan)
                if default_path.exists():
                    return default_path
            except FileNotFoundError:
                pass

        plans = self.list_plans()
        if not plans:
            raise FileNotFoundError("no plans found in .ralphite/plans")
        return plans[0]

    def goal_to_plan(self, goal: str, filename_hint: str = "goal") -> Path:
        plan = make_goal_plan(goal)
        from ralphite_engine.templates import dump_yaml, versioned_filename

        filename = versioned_filename(plan["plan_id"], filename_hint)
        path = self.paths["plans"] / filename
        path.write_text(dump_yaml(plan), encoding="utf-8")
        return path

    def collect_requirements(self, plan_ref: str | None = None, plan_content: str | None = None) -> dict[str, list[str]]:
        if plan_content is None:
            path = self._resolve_plan_path(plan_ref)
            plan_content = path.read_text(encoding="utf-8")
        plan = parse_plan_yaml(plan_content)
        tools = sorted({item for agent in plan.agents for item in agent.tools_allow if item.startswith("tool:")})
        mcps = sorted({item for agent in plan.agents for item in agent.tools_allow if item.startswith("mcp:")})
        return {"tools": tools, "mcps": mcps}

    def _persist_runtime_state(self, handle: RuntimeHandle, status: str) -> None:
        state = RunPersistenceState(
            run_id=handle.run.id,
            status=status,
            plan_path=handle.run.plan_path,
            run=handle.run,
            loop_counts=handle.loop_counts,
            last_seq=handle.seq,
        )
        self.run_store.write_state(state)
        self.history.upsert(handle.run)

    def _checkpoint(self, handle: RuntimeHandle, status: str = "running") -> None:
        self._persist_runtime_state(handle, "checkpointing")
        checkpoint = RunCheckpoint(
            run_id=handle.run.id,
            status=status,
            plan_path=handle.run.plan_path,
            last_seq=handle.seq,
            loop_counts=dict(handle.loop_counts),
            retry_count=handle.run.retry_count,
            node_attempts={node_id: node.attempt_count for node_id, node in handle.run.nodes.items()},
            node_statuses={node_id: node.status for node_id, node in handle.run.nodes.items()},
            active_node_id=handle.run.active_node_id,
        )
        self.run_store.write_checkpoint(checkpoint)
        self._persist_runtime_state(handle, status)

    def start_run(
        self,
        *,
        plan_ref: str | None = None,
        plan_content: str | None = None,
        permission_snapshot: dict[str, list[str]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        source_path = self._resolve_plan_path(plan_ref) if plan_content is None else self.paths["plans"] / "inline.yaml"
        content = plan_content if plan_content is not None else source_path.read_text(encoding="utf-8")

        valid, issues, summary = validate_plan_content(content)
        if not valid:
            raise ValueError(f"validation_error: {json.dumps(issues)}")

        plan = parse_plan_yaml(content)
        compile_plan(plan)

        run_id = str(uuid4())
        nodes = {
            node.id: NodeRuntimeState(
                node_id=node.id,
                kind=node.kind.value,
                group=node.group,
                status="queued",
                attempt_count=0,
                depends_on=list(node.depends_on),
            )
            for node in plan.graph.nodes
        }

        snapshot = permission_snapshot or self.default_permission_snapshot()
        run = RunViewState(
            id=run_id,
            plan_path=str(source_path),
            status="queued",
            started_at=datetime.now(timezone.utc).isoformat(),
            nodes=nodes,
            metadata={"plan": summary, "permission_snapshot": snapshot, **(metadata or {})},
        )

        if not self.run_store.acquire_lock(run_id):
            raise RuntimeError(f"run already locked: {run_id}")

        handle = RuntimeHandle(
            run=run,
            plan=plan,
            permission_snapshot=snapshot,
            loop_counts={loop.id: 0 for loop in plan.graph.loops},
        )

        self.active[run_id] = handle
        self._persist_runtime_state(handle, "queued")
        handle.thread = threading.Thread(target=self._execute_run, args=(handle,), daemon=False)
        handle.thread.start()
        return run_id

    def get_run(self, run_id: str) -> RunViewState | None:
        handle = self.active.get(run_id)
        if handle:
            return handle.run
        state = self.run_store.load_state(run_id)
        if state:
            return state.run
        return self.history.get(run_id)

    def load_run_state(self, run_id: str) -> RunViewState:
        run = self.get_run(run_id)
        if not run:
            raise ValueError("run not found")
        return run

    def list_history(self, limit: int = 20, query: str | None = None) -> list[RunViewState]:
        return self.history.list(limit=limit, query=query)

    def pause_run(self, run_id: str) -> bool:
        handle = self.active.get(run_id)
        if not handle or handle.finished_event.is_set():
            return False
        handle.pause_event.set()
        handle.run.status = "paused"
        self._emit(handle, stage="orchestrator", event="RUN_PAUSED", level="warn", message="run paused")
        self._persist_runtime_state(handle, "paused")
        return True

    def resume_run(self, run_id: str) -> bool:
        handle = self.active.get(run_id)
        if handle and not handle.finished_event.is_set() and handle.thread and handle.thread.is_alive():
            handle.pause_event.clear()
            handle.run.status = "running"
            self._emit(handle, stage="orchestrator", event="RUN_RESUMED", level="info", message="run resumed")
            self._persist_runtime_state(handle, "running")
            return True
        return self.resume_from_checkpoint(run_id)

    def cancel_run(self, run_id: str) -> bool:
        handle = self.active.get(run_id)
        if not handle or handle.finished_event.is_set():
            return False
        handle.cancel_event.set()
        self._emit(handle, stage="orchestrator", event="RUN_CANCEL_REQUESTED", level="warn", message="run cancellation requested")
        self._persist_runtime_state(handle, handle.run.status)
        return True

    def rerun_failed(self, run_id: str) -> str:
        previous = self.history.get(run_id)
        if not previous:
            raise ValueError("run not found")
        return self.start_run(
            plan_ref=previous.plan_path,
            metadata={"replay_of": run_id, "mode": "rerun_failed"},
        )

    def list_recoverable_runs(self) -> list[str]:
        states = [state for run_id in self.run_store.list_run_ids() if (state := self.run_store.load_state(run_id)) is not None]
        return recoverable_run_ids(states, lock_is_stale=self.run_store.lock_is_stale)

    def recover_run(self, run_id: str) -> bool:
        if run_id in self.active:
            return True

        state = self.run_store.load_state(run_id)
        if not state:
            return False

        checkpoint = self.run_store.load_checkpoint(run_id)
        paused_state = to_paused_for_recovery(state, checkpoint)
        events = self.run_store.load_events(run_id)
        run = paused_state.run
        run.events = events

        plan_content = Path(run.plan_path).read_text(encoding="utf-8")
        plan = parse_plan_yaml(plan_content)
        compile_plan(plan)

        snapshot = run.metadata.get("permission_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = self.default_permission_snapshot()

        handle = RuntimeHandle(
            run=run,
            plan=plan,
            permission_snapshot=snapshot,
            seq=paused_state.last_seq,
            loop_counts=dict(paused_state.loop_counts),
        )
        handle.pause_event.set()
        self.active[run_id] = handle
        self._persist_runtime_state(handle, "paused")
        return True

    def resume_from_checkpoint(self, run_id: str) -> bool:
        if run_id not in self.active and not self.recover_run(run_id):
            return False

        handle = self.active[run_id]
        if handle.thread and handle.thread.is_alive():
            return False

        if not self.run_store.acquire_lock(run_id):
            if not self.run_store.lock_is_stale(run_id):
                return False
            self.run_store.release_lock(run_id)
            if not self.run_store.acquire_lock(run_id):
                return False

        handle.pause_event.clear()
        handle.cancel_event.clear()
        handle.finished_event.clear()
        handle.run.status = "running"
        self._emit(handle, stage="orchestrator", event="RUN_RESUME_FROM_CHECKPOINT", level="info", message="run resumed from checkpoint")
        self._persist_runtime_state(handle, "running")
        handle.thread = threading.Thread(target=self._execute_run, args=(handle,), daemon=False)
        handle.thread.start()
        return True

    def wait_for_run(self, run_id: str, timeout: float | None = None) -> bool:
        handle = self.active.get(run_id)
        if not handle:
            return False
        return handle.finished_event.wait(timeout=timeout)

    def stream_events(self, run_id: str, after_seq: int = 0) -> Generator[dict[str, Any], None, None]:
        handle = self.active.get(run_id)
        if not handle:
            events = self.run_store.load_events(run_id)
            if not events:
                saved = self.history.get(run_id)
                if not saved:
                    return
                events = saved.events
            for event in events:
                if int(event.get("id", 0)) > after_seq:
                    yield event
            return

        seen_ids: set[int] = set()
        for event in handle.run.events:
            event_id = int(event.get("id", 0))
            if event_id > after_seq:
                seen_ids.add(event_id)
                yield event

        while True:
            if handle.finished_event.is_set() and handle.event_queue.empty():
                break
            try:
                event = handle.event_queue.get(timeout=0.25)
                event_id = int(event.get("id", 0))
                if event_id <= after_seq or event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
                if event_id > after_seq:
                    yield event
            except Empty:
                continue

    def poll_events(self, run_id: str) -> list[dict[str, Any]]:
        handle = self.active.get(run_id)
        if not handle:
            return []
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(handle.event_queue.get_nowait())
            except Empty:
                break
        return events

    def _emit(
        self,
        handle: RuntimeHandle,
        *,
        stage: str,
        event: str,
        level: str,
        message: str,
        group: str | None = None,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        handle.seq += 1
        payload = {
            "id": handle.seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": handle.run.id,
            "group": group,
            "task_id": task_id,
            "stage": stage,
            "event": event,
            "level": level,
            "message": message,
            "meta": meta or {},
        }
        handle.run.events.append(payload)
        handle.event_queue.put(payload)
        self.run_store.append_event(handle.run.id, payload)

    def _tool_allowed(self, tool_id: str, snapshot: dict[str, list[str]]) -> bool:
        deny = set(snapshot.get("deny_tools", []))
        allow = set(snapshot.get("allow_tools", []))
        if tool_id in deny:
            return False
        if not allow or "tool:*" in allow:
            return True
        return tool_id in allow

    def _mcp_allowed(self, mcp_id: str, snapshot: dict[str, list[str]]) -> bool:
        deny = set(snapshot.get("deny_mcps", []))
        allow = set(snapshot.get("allow_mcps", []))
        if mcp_id in deny:
            return False
        if not allow or "mcp:*" in allow:
            return True
        return mcp_id in allow

    def _execute_agent(self, node: dict[str, Any], snapshot: dict[str, list[str]]) -> tuple[bool, dict[str, Any]]:
        requested = list(node.get("agent", {}).get("tools_allow") or [])
        denied: list[str] = []
        for item in requested:
            if item.startswith("tool:") and not self._tool_allowed(item, snapshot):
                denied.append(item)
            if item.startswith("mcp:") and not self._mcp_allowed(item, snapshot):
                denied.append(item)

        if denied:
            return False, {"reason": "permission_denied", "denied": denied}

        task = str(node.get("task") or "")
        if "[fail]" in task.lower():
            return False, {"reason": "task_marker_failure", "task": task}

        time.sleep(float(os.getenv("RALPHITE_RUNNER_SIMULATED_TASK_SECONDS", "0.2")))
        return True, {
            "summary": f"Executed task: {task[:120]}",
            "agent_id": node.get("agent_id"),
            "model": node.get("agent", {}).get("model"),
        }

    def _reset_subgraph(self, run: RunViewState, start_node: str) -> list[str]:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for node in run.nodes.values():
            for dep in node.depends_on:
                adjacency[dep].append(node.node_id)

        touched: list[str] = []
        queue = deque([start_node])
        visited: set[str] = set()
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            rec = run.nodes.get(nid)
            if rec and rec.status in {"succeeded", "failed", "blocked"}:
                rec.status = "queued"
                rec.result = None
                touched.append(nid)
            for nxt in adjacency.get(nid, []):
                queue.append(nxt)
        return touched

    def _execute_gate(
        self,
        handle: RuntimeHandle,
        node_data: dict[str, Any],
        attempt_count: int,
        loop_max: dict[str, int],
    ) -> tuple[str, dict[str, Any]]:
        gate = node_data.get("gate") or {}
        pass_if = str(gate.get("pass_if", "")).lower()

        decision = "pass"
        retry_once_enabled = os.getenv("RALPHITE_GATE_RETRY_ONCE", "1") == "1"
        if retry_once_enabled and "all_acceptance_checks_pass" in pass_if and attempt_count == 1:
            decision = "retry"
        if "always_fail" in pass_if:
            decision = "fail"

        if decision == "pass":
            return "pass", {"pass_if": gate.get("pass_if")}
        if decision == "fail":
            return "fail", {"pass_if": gate.get("pass_if")}

        retry_edges = [
            edge
            for edge in (handle.run.metadata.get("edges") or [])
            if edge.get("from") == node_data.get("id") and edge.get("when") == EdgeWhen.RETRY.value
        ]
        touched: list[str] = []
        exhausted = False
        for edge in retry_edges:
            loop_id = edge.get("loop_id")
            target = edge.get("to")
            if loop_id:
                count = int(handle.loop_counts.get(loop_id, 0))
                max_count = int(loop_max.get(loop_id, 1))
                if count >= max_count:
                    exhausted = True
                    continue
                handle.loop_counts[loop_id] = count + 1
            if isinstance(target, str):
                touched.extend(self._reset_subgraph(handle.run, target))

        if exhausted and not touched:
            return "retry_exhausted", {"loop_counts": handle.loop_counts}

        handle.run.retry_count += 1
        return "retry", {"loop_counts": handle.loop_counts, "touched_nodes": touched}

    def _write_artifacts(self, run: RunViewState) -> ArtifactIndex:
        artifacts_dir = self.paths["artifacts"] / run.id
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        succeeded = len([n for n in run.nodes.values() if n.status == "succeeded"])
        failed = len([n for n in run.nodes.values() if n.status == "failed"])
        blocked = len([n for n in run.nodes.values() if n.status == "blocked"])

        report = "\n".join(
            [
                f"# Run {run.id} Summary",
                "",
                f"Status: **{run.status}**",
                f"Succeeded nodes: {succeeded}",
                f"Failed nodes: {failed}",
                f"Blocked nodes: {blocked}",
                "",
                "## Timeline",
            ]
            + [f"- [{evt['level']}] {evt['event']}: {evt['message']}" for evt in run.events]
        )
        report_path = artifacts_dir / "final_report.md"
        report_path.write_text(report, encoding="utf-8")

        bundle = {
            "run_id": run.id,
            "status": run.status,
            "plan_path": run.plan_path,
            "retry_count": run.retry_count,
            "nodes": {k: v.model_dump(mode="json") for k, v in run.nodes.items()},
        }
        bundle_path = artifacts_dir / "machine_bundle.json"
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

        items = [
            {"id": "final_report", "path": str(report_path), "format": "markdown"},
            {"id": "machine_bundle", "path": str(bundle_path), "format": "json"},
        ]
        run.artifacts = items
        return ArtifactIndex(run_id=run.id, artifacts_dir=str(artifacts_dir), items=items)

    def _execute_run(self, handle: RuntimeHandle) -> None:
        run = handle.run
        plan = handle.plan

        node_payload = {node.id: node.model_dump(mode="json") for node in plan.graph.nodes}
        agent_map = {agent.id: agent.model_dump(mode="json") for agent in plan.agents}
        for node_id, payload in node_payload.items():
            if payload.get("agent_id"):
                payload["agent"] = agent_map.get(payload["agent_id"], {})

        run.metadata["edges"] = [edge.model_dump(mode="json", by_alias=True) for edge in plan.graph.edges]
        for loop in plan.graph.loops:
            handle.loop_counts.setdefault(loop.id, 0)
        loop_max = {loop.id: int(loop.max_iterations) for loop in plan.graph.loops}

        max_steps = int(plan.constraints.max_total_steps)
        max_runtime = int(plan.constraints.max_runtime_seconds)
        fail_fast = bool(plan.constraints.fail_fast)

        steps = 0
        started_at = time.time()

        if run.status != "running":
            run.status = "running"
            if any(evt.get("event") == "RUN_STARTED" for evt in run.events):
                self._emit(handle, stage="plan", event="RUN_RECOVERED", level="info", message="run recovered")
            else:
                self._emit(handle, stage="plan", event="RUN_STARTED", level="info", message="run started")
        self._persist_runtime_state(handle, "running")

        try:
            while True:
                if handle.cancel_event.is_set():
                    run.status = "cancelled"
                    for node in run.nodes.values():
                        if node.status in {"queued", "running"}:
                            node.status = "blocked"
                    self._emit(handle, stage="summary", event="RUN_DONE", level="warn", message="run cancelled")
                    break

                if time.time() - started_at > max_runtime:
                    run.status = "failed"
                    self._emit(handle, stage="summary", event="RUN_TIMEOUT", level="error", message="run exceeded max runtime")
                    break

                if handle.pause_event.is_set():
                    run.status = "paused"
                    self._persist_runtime_state(handle, "paused")
                    time.sleep(0.1)
                    continue

                ready_nodes = [
                    node
                    for node in plan.graph.nodes
                    if run.nodes[node.id].status == "queued"
                    and all(run.nodes.get(dep) and run.nodes[dep].status == "succeeded" for dep in node.depends_on)
                ]

                if not ready_nodes:
                    queued = any(node.status == "queued" for node in run.nodes.values())
                    running = any(node.status == "running" for node in run.nodes.values())
                    if not queued and not running:
                        break
                    if queued and not running:
                        for node in run.nodes.values():
                            if node.status == "queued":
                                node.status = "blocked"
                        break
                    time.sleep(0.05)
                    continue

                current = ready_nodes[0]
                rec = run.nodes[current.id]
                rec.status = "running"
                rec.attempt_count += 1
                run.active_node_id = current.id
                steps += 1

                self._emit(
                    handle,
                    stage="task",
                    event="NODE_STARTED",
                    level="info",
                    message="node started",
                    group=current.group,
                    task_id=current.id,
                    meta={"attempt": rec.attempt_count},
                )

                if current.kind.value == "agent":
                    success, result = self._execute_agent(node_payload[current.id], handle.permission_snapshot)
                    if success:
                        rec.status = "succeeded"
                        rec.result = result
                        self._emit(
                            handle,
                            stage="task",
                            event="NODE_RESULT",
                            level="info",
                            message="node completed",
                            group=current.group,
                            task_id=current.id,
                            meta={"status": rec.status, "result": result},
                        )
                    else:
                        rec.status = "failed"
                        rec.result = result
                        advice = classify_failure(str(result.get("reason", "runtime_error")))
                        self._emit(
                            handle,
                            stage="task",
                            event="NODE_RESULT",
                            level="error",
                            message=f"{advice.title}: {advice.message}",
                            group=current.group,
                            task_id=current.id,
                            meta={"status": rec.status, "reason": result.get("reason"), "next_action": advice.next_action},
                        )
                        if fail_fast:
                            for node in run.nodes.values():
                                if node.status == "queued":
                                    node.status = "blocked"

                elif current.kind.value == "gate":
                    decision, gate_meta = self._execute_gate(handle, node_payload[current.id], rec.attempt_count, loop_max)
                    rec.result = gate_meta
                    if decision == "pass":
                        rec.status = "succeeded"
                        self._emit(
                            handle,
                            stage="orchestrator",
                            event="GATE_PASS",
                            level="info",
                            message="gate passed",
                            group=current.group,
                            task_id=current.id,
                            meta=gate_meta,
                        )
                    elif decision in {"fail", "retry_exhausted"}:
                        rec.status = "failed"
                        self._emit(
                            handle,
                            stage="orchestrator",
                            event="GATE_FAIL",
                            level="error",
                            message="gate failed",
                            group=current.group,
                            task_id=current.id,
                            meta=gate_meta,
                        )
                        if fail_fast:
                            for node in run.nodes.values():
                                if node.status == "queued":
                                    node.status = "blocked"
                    else:
                        rec.status = "succeeded"
                        self._emit(
                            handle,
                            stage="orchestrator",
                            event="GATE_RETRY",
                            level="warn",
                            message="gate requested retry",
                            group=current.group,
                            task_id=current.id,
                            meta=gate_meta,
                        )

                    self._emit(
                        handle,
                        stage="task",
                        event="NODE_RESULT",
                        level="info" if rec.status == "succeeded" else "error",
                        message="node completed",
                        group=current.group,
                        task_id=current.id,
                        meta={"status": rec.status, "decision": decision},
                    )
                else:
                    rec.status = "failed"
                    advice = classify_failure(f"unknown_node_kind:{current.kind.value}")
                    self._emit(
                        handle,
                        stage="task",
                        event="NODE_RESULT",
                        level="error",
                        message=f"{advice.title}: {advice.message}",
                        group=current.group,
                        task_id=current.id,
                        meta={"reason": f"unknown_node_kind:{current.kind.value}", "next_action": advice.next_action},
                    )

                run.active_node_id = None
                self._checkpoint(handle, status="running")

                if steps >= max_steps:
                    run.status = "failed"
                    self._emit(handle, stage="summary", event="RUN_LIMIT_REACHED", level="error", message="run exceeded max steps")
                    break

            if run.status not in {"failed", "cancelled", "paused"}:
                run.status = "failed" if any(node.status == "failed" for node in run.nodes.values()) else "succeeded"

            if run.status in {"succeeded", "failed", "cancelled"}:
                run.completed_at = datetime.now(timezone.utc).isoformat()
                artifacts = self._write_artifacts(run)
                self._emit(
                    handle,
                    stage="summary",
                    event="RUN_SUMMARY",
                    level="info",
                    message="run summary emitted",
                    meta={
                        "status": run.status,
                        "artifacts": artifacts.items,
                        "task_summary": {
                            "succeeded": len([n for n in run.nodes.values() if n.status == "succeeded"]),
                            "failed": len([n for n in run.nodes.values() if n.status == "failed"]),
                            "blocked": len([n for n in run.nodes.values() if n.status == "blocked"]),
                        },
                    },
                )
                self._emit(
                    handle,
                    stage="summary",
                    event="RUN_DONE",
                    level="info" if run.status == "succeeded" else "error",
                    message="run completed",
                    meta={"status": run.status},
                )

            final_state = run.status if run.status in {"succeeded", "failed", "cancelled", "paused"} else "running"
            self._checkpoint(handle, status=final_state)
        finally:
            if run.status in {"succeeded", "failed", "cancelled"}:
                self.run_store.release_lock(run.id)
            handle.finished_event.set()
