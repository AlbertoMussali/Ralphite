from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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
from ralphite_engine.git_worktree import GitWorktreeManager
from ralphite_engine.models import ArtifactIndex, NodeRuntimeState, RunCheckpoint, RunPersistenceState, RunViewState
from ralphite_engine.recovery import recoverable_run_ids, to_paused_for_recovery
from ralphite_engine.run_store import RunStore
from ralphite_engine.store import HistoryStore
from ralphite_engine.structure_compiler import RuntimeExecutionPlan, RuntimeNodeSpec, compile_execution_structure
from ralphite_engine.task_parser import parse_task_file
from ralphite_engine.taxonomy import classify_failure
from ralphite_engine.templates import make_goal_plan, seed_starter_if_missing
from ralphite_engine.validation import parse_plan_yaml, resolve_task_source_path, validate_plan_content
from ralphite_schemas.plan_v2 import AgentProfileSpec, PlanSpecV2
from ralphite_schemas.validation import compile_plan


@dataclass
class RuntimeHandle:
    run: RunViewState
    plan: PlanSpecV2
    runtime: RuntimeExecutionPlan
    profile_map: dict[str, AgentProfileSpec]
    permission_snapshot: dict[str, list[str]]
    event_queue: Queue[dict[str, Any]] = field(default_factory=Queue)
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    seq: int = 0
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
        from ralphite_engine.templates import dump_yaml, make_starter_task_markdown, versioned_filename

        filename = versioned_filename(plan["plan_id"], filename_hint)
        task_filename = f"{Path(filename).stem}.tasks.md"
        task_path = self.paths["plans"] / task_filename
        task_path.write_text(make_starter_task_markdown(goal), encoding="utf-8")
        plan["task_source"]["path"] = str(task_path.relative_to(self.workspace_root)).replace("\\", "/")
        path = self.paths["plans"] / filename
        path.write_text(dump_yaml(plan), encoding="utf-8")
        return path

    def _runtime_metadata(self, runtime: RuntimeExecutionPlan) -> dict[str, Any]:
        lane_map: dict[str, str] = {}
        phase_map: dict[str, str] = {}
        role_map: dict[str, str] = {}
        phase_nodes: dict[str, list[str]] = defaultdict(list)

        for node in runtime.nodes:
            lane_map[node.id] = node.lane
            phase_map[node.id] = node.phase
            role_map[node.id] = node.role
            phase_nodes[node.phase].append(node.id)

        return {
            "plan_version": 2,
            "v2_lane_map": lane_map,
            "v2_phase_map": phase_map,
            "v2_role_map": role_map,
            "v2_phase_nodes": dict(phase_nodes),
            "v2_parallel_limit": int(runtime.parallel_limit),
            "task_parse_issues": list(runtime.task_parse_issues),
            "recovery": {
                "status": "none",
                "options": ["manual", "agent_best_effort", "abort_phase"],
                "selected_mode": None,
                "prompt": None,
                "attempts": 0,
            },
        }

    def _materialize_runtime_plan(self, plan: PlanSpecV2) -> tuple[RuntimeExecutionPlan, dict[str, Any]]:
        task_file = resolve_task_source_path(plan.task_source.path, self.workspace_root)
        tasks, parse_issues = parse_task_file(task_file)
        runtime, compile_issues = compile_execution_structure(plan, tasks, task_parse_issues=parse_issues)
        if runtime is None or compile_issues:
            details = [
                {"code": "execution_structure.invalid", "message": issue, "path": "execution_structure"}
                for issue in compile_issues
            ]
            raise ValueError(f"validation_error: {json.dumps(details)}")
        return runtime, self._runtime_metadata(runtime)

    def collect_requirements(self, plan_ref: str | None = None, plan_content: str | None = None) -> dict[str, list[str]]:
        if plan_content is None:
            path = self._resolve_plan_path(plan_ref)
            plan_content = path.read_text(encoding="utf-8")
        plan = parse_plan_yaml(plan_content)
        tools = sorted({item for profile in plan.agent_profiles for item in profile.tools_allow if item.startswith("tool:")})
        mcps = sorted({item for profile in plan.agent_profiles for item in profile.tools_allow if item.startswith("mcp:")})
        return {"tools": tools, "mcps": mcps}

    def _persist_runtime_state(self, handle: RuntimeHandle, status: str) -> None:
        state = RunPersistenceState(
            run_id=handle.run.id,
            status=status,
            plan_path=handle.run.plan_path,
            run=handle.run,
            loop_counts={},
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
            loop_counts={},
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

        valid, issues, summary = validate_plan_content(content, workspace_root=self.workspace_root)
        if not valid:
            raise ValueError(f"validation_error: {json.dumps(issues)}")

        plan_document = parse_plan_yaml(content)
        compile_plan(plan_document)
        runtime, runtime_meta = self._materialize_runtime_plan(plan_document)

        profile_map = {profile.id: profile for profile in plan_document.agent_profiles}
        nodes = {
            node.id: NodeRuntimeState(
                node_id=node.id,
                kind=node.kind,
                group=node.group,
                status="queued",
                attempt_count=0,
                depends_on=list(node.depends_on),
            )
            for node in runtime.nodes
        }

        snapshot = permission_snapshot or self.default_permission_snapshot()
        git_manager = GitWorktreeManager(self.workspace_root, "pending")
        run_id = str(uuid4())
        run = RunViewState(
            id=run_id,
            plan_path=str(source_path),
            status="queued",
            started_at=datetime.now(timezone.utc).isoformat(),
            nodes=nodes,
            metadata={
                "plan": summary,
                "permission_snapshot": snapshot,
                **runtime_meta,
                "git_state": git_manager.bootstrap_state(),
                **(metadata or {}),
            },
        )

        if not self.run_store.acquire_lock(run_id):
            raise RuntimeError(f"run already locked: {run_id}")

        handle = RuntimeHandle(
            run=run,
            plan=plan_document,
            runtime=runtime,
            profile_map=profile_map,
            permission_snapshot=snapshot,
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
        self._emit(
            handle,
            stage="orchestrator",
            event="RUN_CANCEL_REQUESTED",
            level="warn",
            message="run cancellation requested",
        )
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
        plan_document = parse_plan_yaml(plan_content)
        compile_plan(plan_document)
        runtime, runtime_meta = self._materialize_runtime_plan(plan_document)

        run.metadata.setdefault("plan_version", runtime_meta.get("plan_version", 2))
        run.metadata.setdefault("v2_parallel_limit", runtime_meta.get("v2_parallel_limit", 1))
        run.metadata.setdefault("v2_lane_map", runtime_meta.get("v2_lane_map", {}))
        run.metadata.setdefault("v2_phase_map", runtime_meta.get("v2_phase_map", {}))
        run.metadata.setdefault("v2_role_map", runtime_meta.get("v2_role_map", {}))
        run.metadata.setdefault("v2_phase_nodes", runtime_meta.get("v2_phase_nodes", {}))
        run.metadata.setdefault("task_parse_issues", runtime_meta.get("task_parse_issues", []))
        run.metadata.setdefault(
            "recovery",
            {
                "status": "none",
                "options": ["manual", "agent_best_effort", "abort_phase"],
                "selected_mode": None,
                "prompt": None,
                "attempts": 0,
            },
        )
        run.metadata.setdefault("git_state", GitWorktreeManager(self.workspace_root, run_id).bootstrap_state())

        snapshot = run.metadata.get("permission_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = self.default_permission_snapshot()

        profile_map = {profile.id: profile for profile in plan_document.agent_profiles}
        handle = RuntimeHandle(
            run=run,
            plan=plan_document,
            runtime=runtime,
            profile_map=profile_map,
            permission_snapshot=snapshot,
            seq=paused_state.last_seq,
        )
        handle.pause_event.set()
        self.active[run_id] = handle
        self._persist_runtime_state(handle, "paused")
        return True

    def set_recovery_mode(self, run_id: str, mode: str, prompt: str | None = None) -> bool:
        handle = self.active.get(run_id)
        if not handle:
            if not self.recover_run(run_id):
                return False
            handle = self.active.get(run_id)
        if not handle:
            return False

        allowed = {"manual", "agent_best_effort", "abort_phase"}
        if mode not in allowed:
            return False

        recovery = handle.run.metadata.setdefault("recovery", {})
        recovery["selected_mode"] = mode
        recovery["prompt"] = prompt
        recovery["status"] = "selected"
        self._emit(
            handle,
            stage="orchestrator",
            event="RECOVERY_MODE_SELECTED",
            level="warn",
            message=f"recovery mode selected: {mode}",
            meta={"mode": mode, "has_prompt": bool(prompt)},
        )
        self._checkpoint(handle, status="paused_recovery_required")
        return True

    def resume_from_checkpoint(self, run_id: str) -> bool:
        if run_id not in self.active and not self.recover_run(run_id):
            return False

        handle = self.active[run_id]
        if handle.thread and handle.thread.is_alive():
            return False

        recovery = handle.run.metadata.get("recovery", {})
        if handle.run.status == "paused_recovery_required":
            selected = recovery.get("selected_mode")
            if selected == "abort_phase":
                handle.run.status = "failed"
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="RECOVERY_ABORTED",
                    level="error",
                    message="recovery aborted by user",
                )
                self._finalize_terminal_run(handle)
                return True
            if selected not in {"manual", "agent_best_effort"}:
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
        self._emit(
            handle,
            stage="orchestrator",
            event="RUN_RESUME_FROM_CHECKPOINT",
            level="info",
            message="run resumed from checkpoint",
        )
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

    def _execute_agent(
        self,
        node: RuntimeNodeSpec,
        profile: AgentProfileSpec,
        snapshot: dict[str, list[str]],
    ) -> tuple[bool, dict[str, Any]]:
        requested = list(profile.tools_allow or [])
        denied: list[str] = []
        for item in requested:
            if item.startswith("tool:") and not self._tool_allowed(item, snapshot):
                denied.append(item)
            if item.startswith("mcp:") and not self._mcp_allowed(item, snapshot):
                denied.append(item)

        if denied:
            return False, {"reason": "permission_denied", "denied": denied}

        task = str(node.task or "")
        if "[fail]" in task.lower():
            return False, {"reason": "task_marker_failure", "task": task}

        time.sleep(float(os.getenv("RALPHITE_RUNNER_SIMULATED_TASK_SECONDS", "0.2")))
        return True, {
            "summary": f"Executed task: {task[:120]}",
            "agent_profile_id": profile.id,
            "model": profile.model,
            "role": node.role,
            "phase": node.phase,
            "lane": node.lane,
        }

    def _emit_v2_node_started(self, handle: RuntimeHandle, node: RuntimeNodeSpec) -> None:
        metadata = handle.run.metadata
        phase = node.phase
        lane = node.lane

        phase_started = set(metadata.get("v2_phase_started", []))
        if phase and phase not in phase_started:
            self._emit(handle, stage="plan", event="PHASE_STARTED", level="info", message=f"phase started: {phase}", group=phase)
            phase_started.add(phase)
            metadata["v2_phase_started"] = sorted(phase_started)

        if node.role == "worker":
            lane_started = set(metadata.get("v2_lane_started", []))
            lane_key = f"{phase}:{lane}"
            if lane_key not in lane_started:
                self._emit(
                    handle,
                    stage="plan",
                    event="LANE_STARTED",
                    level="info",
                    message=f"lane started: {lane}",
                    group=phase,
                    meta={"lane": lane},
                )
                lane_started.add(lane_key)
                metadata["v2_lane_started"] = sorted(lane_started)
            self._emit(
                handle,
                stage="task",
                event="WORKER_STARTED",
                level="info",
                message="worker task started",
                group=phase,
                task_id=node.id,
                meta={"lane": lane},
            )
        elif node.role == "orchestrator_pre":
            self._emit(
                handle,
                stage="orchestrator",
                event="ORCH_PRE_STARTED",
                level="info",
                message="pre-orchestrator started",
                group=phase,
                task_id=node.id,
            )
        elif node.role == "orchestrator_post":
            self._emit(
                handle,
                stage="orchestrator",
                event="ORCH_POST_STARTED",
                level="info",
                message="post-orchestrator started",
                group=phase,
                task_id=node.id,
            )

    def _emit_v2_node_completed(self, handle: RuntimeHandle, node: RuntimeNodeSpec, success: bool) -> None:
        metadata = handle.run.metadata
        phase = node.phase

        if node.role == "worker" and success:
            self._emit(
                handle,
                stage="task",
                event="WORKER_MERGED",
                level="info",
                message="worker output integrated to phase branch",
                group=phase,
                task_id=node.id,
                meta={"lane": node.lane},
            )
        elif node.role == "orchestrator_pre":
            self._emit(
                handle,
                stage="orchestrator",
                event="ORCH_PRE_DONE",
                level="info" if success else "error",
                message="pre-orchestrator completed" if success else "pre-orchestrator failed",
                group=phase,
                task_id=node.id,
            )
        elif node.role == "orchestrator_post":
            self._emit(
                handle,
                stage="orchestrator",
                event="ORCH_POST_DONE",
                level="info" if success else "error",
                message="post-orchestrator completed" if success else "post-orchestrator failed",
                group=phase,
                task_id=node.id,
                meta={"commit_strategy": "preserve_worker_commits", "cleanup_done": bool(success)},
            )

        phase_done = set(metadata.get("v2_phase_done", []))
        phase_node_ids = list(metadata.get("v2_phase_nodes", {}).get(phase, []))
        if phase and phase not in phase_done and phase_node_ids:
            statuses = [handle.run.nodes[node_id].status for node_id in phase_node_ids if node_id in handle.run.nodes]
            terminal = {"succeeded", "failed", "blocked"}
            if statuses and all(status in terminal for status in statuses):
                self._emit(
                    handle,
                    stage="summary",
                    event="PHASE_DONE",
                    level="info" if all(status == "succeeded" for status in statuses) else "error",
                    message=f"phase completed: {phase}",
                    group=phase,
                )
                phase_done.add(phase)
                metadata["v2_phase_done"] = sorted(phase_done)

    def _start_node_execution(self, handle: RuntimeHandle, node: RuntimeNodeSpec) -> None:
        rec = handle.run.nodes[node.id]
        rec.status = "running"
        rec.attempt_count += 1
        handle.run.active_node_id = node.id
        self._emit(
            handle,
            stage="task",
            event="NODE_STARTED",
            level="info",
            message="node started",
            group=node.group,
            task_id=node.id,
            meta={"attempt": rec.attempt_count},
        )
        self._emit_v2_node_started(handle, node)

    def _handle_recovery_required(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        details: dict[str, Any],
    ) -> None:
        recovery = handle.run.metadata.setdefault("recovery", {})
        recovery["status"] = "required"
        recovery["phase"] = node.phase
        recovery["details"] = details
        recovery["attempts"] = int(recovery.get("attempts") or 0) + 1

        self._emit(
            handle,
            stage="orchestrator",
            event="RECOVERY_REQUIRED",
            level="error",
            message="merge/integration conflict requires recovery",
            group=node.phase,
            task_id=node.id,
            meta=details,
        )

        handle.run.status = "paused_recovery_required"
        handle.run.active_node_id = None
        handle.pause_event.set()

    def _apply_agent_result(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        *,
        success: bool,
        result: dict[str, Any],
        fail_fast: bool,
    ) -> None:
        rec = handle.run.nodes[node.id]
        if success:
            rec.status = "succeeded"
            rec.result = result
            self._emit(
                handle,
                stage="task",
                event="NODE_RESULT",
                level="info",
                message="node completed",
                group=node.group,
                task_id=node.id,
                meta={"status": rec.status, "result": result},
            )
            self._emit_v2_node_completed(handle, node, True)
            return

        rec.status = "failed"
        rec.result = result
        advice = classify_failure(str(result.get("reason", "runtime_error")))
        self._emit(
            handle,
            stage="task",
            event="NODE_RESULT",
            level="error",
            message=f"{advice.title}: {advice.message}",
            group=node.group,
            task_id=node.id,
            meta={"status": rec.status, "reason": result.get("reason"), "next_action": advice.next_action},
        )
        self._emit_v2_node_completed(handle, node, False)

        if fail_fast:
            for queued in handle.run.nodes.values():
                if queued.status == "queued":
                    queued.status = "blocked"

    def _write_artifacts(self, run: RunViewState) -> ArtifactIndex:
        artifacts_dir = self.paths["artifacts"] / run.id
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        succeeded = len([n for n in run.nodes.values() if n.status == "succeeded"])
        failed = len([n for n in run.nodes.values() if n.status == "failed"])
        blocked = len([n for n in run.nodes.values() if n.status == "blocked"])

        phase_done = run.metadata.get("v2_phase_done", [])
        recovery = run.metadata.get("recovery", {})

        report = "\n".join(
            [
                f"# Run {run.id} Summary",
                "",
                f"Status: **{run.status}**",
                f"Succeeded nodes: {succeeded}",
                f"Failed nodes: {failed}",
                f"Blocked nodes: {blocked}",
                f"Completed phases: {', '.join(phase_done) if phase_done else 'none'}",
                f"Recovery status: {recovery.get('status', 'none')}",
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
            "metadata": run.metadata,
        }
        bundle_path = artifacts_dir / "machine_bundle.json"
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

        items = [
            {"id": "final_report", "path": str(report_path), "format": "markdown"},
            {"id": "machine_bundle", "path": str(bundle_path), "format": "json"},
        ]
        run.artifacts = items
        return ArtifactIndex(run_id=run.id, artifacts_dir=str(artifacts_dir), items=items)

    def _choose_batch(self, handle: RuntimeHandle, ready_nodes: list[RuntimeNodeSpec]) -> list[RuntimeNodeSpec]:
        if not ready_nodes:
            return []

        phase_order = {phase.id: idx for idx, phase in enumerate(handle.plan.execution_structure.phases)}
        ready_nodes = sorted(ready_nodes, key=lambda node: (phase_order.get(node.phase, 10_000), node.id))
        first_phase = ready_nodes[0].phase
        same_phase = [node for node in ready_nodes if node.phase == first_phase]

        parallel_limit = max(1, int(handle.runtime.parallel_limit or 1))
        parallel_ready = [node for node in same_phase if node.lane == "parallel"]
        if parallel_ready:
            return parallel_ready[:parallel_limit]

        return [same_phase[0]]

    def _run_node(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
    ) -> tuple[str, dict[str, Any]]:
        profile = handle.profile_map.get(node.agent_profile_id)
        if not profile:
            return "failure", {"reason": "runtime_error", "error": f"unknown agent_profile_id {node.agent_profile_id}"}

        if node.role == "orchestrator_post":
            agent_ok, agent_result = self._execute_agent(node, profile, handle.permission_snapshot)
            if not agent_ok:
                return "failure", agent_result

            recovery = handle.run.metadata.setdefault("recovery", {})
            selected_mode = str(recovery.get("selected_mode") or "manual")
            selected_prompt = recovery.get("prompt")

            if selected_mode == "agent_best_effort":
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="RECOVERY_AGENT_STARTED",
                    level="warn",
                    message="best-effort recovery agent started",
                    group=node.phase,
                    task_id=node.id,
                )

            status, merge_meta = git_manager.integrate_phase(
                handle.run.metadata.setdefault("git_state", {}),
                node.phase,
                recovery_mode=selected_mode,
                recovery_prompt=str(selected_prompt) if selected_prompt else None,
            )

            if selected_mode == "agent_best_effort":
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="RECOVERY_AGENT_DONE",
                    level="info" if status == "success" else "error",
                    message="best-effort recovery agent finished",
                    group=node.phase,
                    task_id=node.id,
                    meta={"status": status},
                )

            if status == "recovery_required":
                return "recovery_required", merge_meta
            if status == "failed":
                return "failure", {"reason": "runtime_error", **merge_meta}

            cleanup = git_manager.cleanup_phase(handle.run.metadata.setdefault("git_state", {}), node.phase)
            return "success", {**agent_result, "integration": merge_meta, "cleanup": cleanup}

        if node.role == "worker":
            git_manager.prepare_phase(handle.run.metadata.setdefault("git_state", {}), node.phase)
            ok, result = self._execute_agent(node, profile, handle.permission_snapshot)
            if not ok:
                return "failure", result

            commit_ok, commit_meta = git_manager.commit_worker(
                handle.run.metadata.setdefault("git_state", {}),
                node.phase,
                node.id,
                f"task({node.source_task_id or node.id}): {node.task[:72]}",
            )
            if not commit_ok:
                return "failure", commit_meta
            return "success", {**result, "worktree": commit_meta}

        ok, result = self._execute_agent(node, profile, handle.permission_snapshot)
        return ("success", result) if ok else ("failure", result)

    def _finalize_terminal_run(self, handle: RuntimeHandle) -> None:
        run = handle.run
        run.completed_at = datetime.now(timezone.utc).isoformat()
        self._write_artifacts(run)
        self._emit(
            handle,
            stage="summary",
            event="RUN_DONE",
            level="info" if run.status == "succeeded" else "error" if run.status == "failed" else "warn",
            message=f"run {run.status}",
        )
        self._persist_runtime_state(handle, run.status)
        self.run_store.release_lock(run.id)
        handle.finished_event.set()

    def _execute_run(self, handle: RuntimeHandle) -> None:
        run = handle.run
        runtime = handle.runtime
        git_manager = GitWorktreeManager(self.workspace_root, run.id)
        run.metadata["git_state"] = git_manager.bootstrap_state(run.metadata.get("git_state"))

        max_steps = int(handle.plan.constraints.max_total_steps)
        max_runtime = int(handle.plan.constraints.max_runtime_seconds)
        fail_fast = bool(handle.plan.constraints.fail_fast)

        steps = 0
        started_at = time.time()
        node_by_id = {node.id: node for node in runtime.nodes}

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
                    break

                if time.time() - started_at > max_runtime:
                    run.status = "failed"
                    self._emit(handle, stage="summary", event="RUN_TIMEOUT", level="error", message="run exceeded max runtime")
                    break

                if handle.pause_event.is_set() and run.status != "paused_recovery_required":
                    run.status = "paused"
                    self._persist_runtime_state(handle, "paused")
                    time.sleep(0.1)
                    continue

                ready_nodes = [
                    node
                    for node in runtime.nodes
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

                batch = self._choose_batch(handle, ready_nodes)
                for current in batch:
                    self._start_node_execution(handle, current)
                steps += len(batch)

                results: dict[str, tuple[str, dict[str, Any]]] = {}
                if len(batch) > 1 and all(node.role == "worker" for node in batch):
                    with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                        futures = {
                            node.id: pool.submit(self._run_node, handle, node, git_manager)
                            for node in batch
                        }
                        for node in batch:
                            try:
                                results[node.id] = futures[node.id].result()
                            except Exception as exc:  # noqa: BLE001
                                results[node.id] = ("failure", {"reason": "runtime_error", "error": str(exc)})
                else:
                    current = batch[0]
                    try:
                        results[current.id] = self._run_node(handle, current, git_manager)
                    except Exception as exc:  # noqa: BLE001
                        results[current.id] = ("failure", {"reason": "runtime_error", "error": str(exc)})

                for current in batch:
                    outcome, result = results[current.id]
                    rec = run.nodes[current.id]

                    if outcome == "recovery_required":
                        rec.status = "queued"
                        rec.result = result
                        self._handle_recovery_required(handle, current, result)
                        break

                    if outcome == "success":
                        self._apply_agent_result(handle, current, success=True, result=result, fail_fast=fail_fast)
                    else:
                        self._apply_agent_result(handle, current, success=False, result=result, fail_fast=fail_fast)

                run.active_node_id = None

                if run.status == "paused_recovery_required":
                    self._checkpoint(handle, status="paused_recovery_required")
                    self._persist_runtime_state(handle, "paused_recovery_required")
                    break

                self._checkpoint(handle, status="running")

                if steps >= max_steps:
                    run.status = "failed"
                    self._emit(handle, stage="summary", event="RUN_LIMIT_REACHED", level="error", message="run exceeded max steps")
                    break

            if run.status == "running":
                failed = any(node.status == "failed" for node in run.nodes.values())
                blocked = any(node.status == "blocked" for node in run.nodes.values())
                if failed or blocked:
                    run.status = "failed"
                else:
                    run.status = "succeeded"

            if run.status == "paused_recovery_required":
                self._persist_runtime_state(handle, "paused_recovery_required")
                self.run_store.release_lock(run.id)
                handle.finished_event.set()
                return

            cleanup_notes = git_manager.cleanup_all(run.metadata.setdefault("git_state", {}))
            if cleanup_notes:
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="CLEANUP_DONE",
                    level="info",
                    message="workspace cleanup completed",
                    meta={"items": cleanup_notes},
                )
            self._finalize_terminal_run(handle)
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            self._emit(
                handle,
                stage="summary",
                event="RUN_INTERNAL_ERROR",
                level="error",
                message=f"run crashed: {exc}",
                meta={"error": str(exc)},
            )
            self._finalize_terminal_run(handle)
