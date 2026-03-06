from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import glob
import json
from pathlib import Path
from queue import Queue
import subprocess
import threading
import time
from typing import Any, Generator
from uuid import uuid4

from ralphite.engine.config import LocalConfig, ensure_workspace_layout, load_config
from ralphite.engine.git_worktree import GitRequiredError
from ralphite.engine.headless_agent import (
    BackendExecutionConfig,
    build_node_prompt,
    execute_headless_agent,
)
from ralphite.engine.git_worktree import GitWorktreeManager
from ralphite.engine.models import (
    ArtifactIndex,
    NodeRuntimeState,
    RunMetrics,
    RunPersistenceState,
    RunViewState,
)
from ralphite.engine.reporting import build_final_report
from ralphite.engine.recovery import to_paused_for_recovery
from ralphite.engine.run_store import RunStore
from ralphite.engine.store import HistoryStore
from ralphite.engine.structure_compiler import (
    RuntimeExecutionPlan,
    RuntimeNodeSpec,
    compile_execution_structure,
)
from ralphite.engine.task_parser import parse_plan_tasks
from ralphite.engine.task_writer import mark_tasks_completed
from ralphite.engine.taxonomy import classify_failure
from ralphite.engine.templates import (
    make_goal_plan,
    seed_starter_if_missing,
    versioned_filename,
)
from ralphite.engine.validation import (
    parse_plan_with_defaults,
    parse_plan_yaml,
    validate_plan_content,
)
from ralphite.schemas.plan import AgentSpec, BehaviorKind, PlanSpec
from ralphite.schemas.validation import compile_plan


@dataclass
class RuntimeHandle:
    run: RunViewState
    plan: PlanSpec
    runtime: RuntimeExecutionPlan
    profile_map: dict[str, AgentSpec]
    permission_snapshot: dict[str, list[str]]
    event_queue: Queue[dict[str, Any]] = field(default_factory=Queue)
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    seq: int = 0
    thread: threading.Thread | None = None


class RunStartBlockedError(RuntimeError):
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details
        super().__init__(str(details.get("detail") or "run start preflight failed"))


class LocalOrchestrator:
    def __init__(self, workspace_root: str | Path, *, bootstrap: bool = True) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.paths = ensure_workspace_layout(self.workspace_root)
        self.config: LocalConfig = load_config(
            self.workspace_root, create_if_missing=bootstrap
        )
        self.history = HistoryStore(self.paths["history"])
        self.run_store = RunStore(self.paths["runs"])
        self.active: dict[str, RuntimeHandle] = {}

        from ralphite.engine.state_manager import RunStateManager
        from ralphite.engine.event_logger import RunEventLogger
        from ralphite.engine.git_orchestrator import GitOrchestrator

        self.state_manager = RunStateManager(self.run_store, self.history)
        self.event_logger = RunEventLogger(self.run_store, self.history, self.active)
        self.git_orchestrator = GitOrchestrator(self.workspace_root)

        if bootstrap:
            seed_starter_if_missing(self.paths["plans"])
        self._bootstrap_recovery_candidates()

    def _bootstrap_recovery_candidates(self) -> None:
        for run_id in self.run_store.list_run_ids():
            state = self.run_store.load_state(run_id)
            if not state:
                continue
            if state.status in {
                "running",
                "checkpointing",
            } and self.run_store.lock_is_stale(run_id):
                recovering = RunPersistenceState(
                    run_id=state.run_id,
                    status="recovering",
                    plan_path=state.plan_path,
                    run=state.run,
                    loop_counts=state.loop_counts,
                    last_seq=state.last_seq,
                )
                self.run_store.write_state(recovering)
                paused = to_paused_for_recovery(
                    recovering, self.run_store.load_checkpoint(run_id)
                )
                self.run_store.write_state(paused)
                self.history.upsert(paused.run)

    def list_plans(self) -> list[Path]:
        return sorted(
            [
                p
                for p in self.paths["plans"].iterdir()
                if p.is_file() and p.suffix.lower() in {".yaml", ".yml"}
            ],
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

    def list_active_run_ids(self) -> list[str]:
        return sorted(self.active.keys())

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

        # Prefer the newest parseable v1 plan.
        for candidate in plans:
            try:
                parse_plan_yaml(
                    candidate.read_text(encoding="utf-8"),
                    workspace_root=self.workspace_root,
                    plan_path=str(candidate),
                )
                return candidate
            except Exception:
                continue
        return plans[0]

    def goal_to_plan(self, goal: str, filename_hint: str = "goal") -> Path:
        plan = make_goal_plan(goal)
        from ralphite.engine.templates import dump_yaml, versioned_filename

        filename = versioned_filename(plan["plan_id"], filename_hint)
        path = self.paths["plans"] / filename
        path.write_text(dump_yaml(plan), encoding="utf-8")
        return path

    def _task_surface_map(self, tasks: list[Any]) -> dict[str, list[str]]:
        shared_keywords = {
            "readme",
            "contributing",
            "user_guide",
            "docs",
            "doc",
            "cli",
            "first-run",
            "references",
            "index",
        }
        mapping: dict[str, list[str]] = {}
        for task in tasks:
            surfaces: set[str] = set()
            for tag in getattr(task, "routing_tags", []) or []:
                value = str(tag).strip().lower()
                if value:
                    surfaces.add(value)
            for artifact in getattr(task, "acceptance_required_artifacts", []) or []:
                path_glob = str(artifact.get("path_glob") or "").strip().lower()
                if not path_glob:
                    continue
                if "/" in path_glob:
                    surfaces.add(path_glob.split("/", 1)[0])
                else:
                    surfaces.add(path_glob)
            text = " ".join(
                [
                    str(getattr(task, "title", "")),
                    str(getattr(task, "description", "")),
                ]
            ).lower()
            for token in shared_keywords:
                if token in text:
                    surfaces.add(token)
            mapping[str(getattr(task, "id", ""))] = sorted(surfaces)
        return mapping

    def _runtime_metadata(
        self, runtime: RuntimeExecutionPlan, *, tasks: list[Any]
    ) -> dict[str, Any]:
        lane_map: dict[str, str] = {}
        phase_map: dict[str, str] = {}
        role_map: dict[str, str] = {}
        phase_nodes: dict[str, list[str]] = defaultdict(list)
        cell_map: dict[str, str] = {}
        team_map: dict[str, str] = {}
        behavior_map: dict[str, str] = {}
        task_surface_map = self._task_surface_map(tasks)
        node_surface_map: dict[str, list[str]] = {}

        for node in runtime.nodes:
            lane_map[node.id] = node.lane
            phase_map[node.id] = node.phase
            role_map[node.id] = node.role
            phase_nodes[node.phase].append(node.id)
            cell_map[node.id] = node.cell_id
            if node.team:
                team_map[node.id] = node.team
            if node.behavior_kind:
                behavior_map[node.id] = node.behavior_kind
            if node.source_task_id:
                node_surface_map[node.id] = list(
                    task_surface_map.get(node.source_task_id, [])
                )

        return {
            "plan_version": 1,
            "lane_map": lane_map,
            "phase_map": phase_map,
            "role_map": role_map,
            "cell_map": cell_map,
            "team_map": team_map,
            "behavior_map": behavior_map,
            "phase_nodes": dict(phase_nodes),
            "parallel_limit": int(runtime.parallel_limit),
            "task_surface_map": task_surface_map,
            "node_surface_map": node_surface_map,
            "task_blocks": [
                {
                    "index": block.index,
                    "kind": block.kind,
                    "cell_id": block.cell_id,
                    "lane": block.lane,
                    "team": block.team,
                    "behavior_id": block.behavior_id,
                    "node_ids": list(block.node_ids),
                    "task_ids": list(block.task_ids),
                }
                for block in runtime.blocks
            ],
            "resolved_execution": {
                "template": runtime.resolved_cells[0].template
                if runtime.resolved_cells
                else "",
                "resolved_cells": [
                    {
                        "id": cell.id,
                        "kind": cell.kind,
                        "lane": cell.lane,
                        "team": cell.team,
                        "behavior_id": cell.behavior_id,
                        "task_ids": list(cell.task_ids),
                        "node_ids": list(cell.node_ids),
                    }
                    for cell in runtime.resolved_cells
                ],
                "resolved_nodes": [
                    {
                        "id": node.id,
                        "cell_id": node.cell_id,
                        "role": node.role,
                        "task_title": node.task,
                        "lane": node.lane,
                        "team": node.team,
                        "block_index": node.block_index,
                        "depends_on": list(node.depends_on),
                        "source_task_id": node.source_task_id,
                        "behavior_id": node.behavior_id,
                        "behavior_kind": node.behavior_kind,
                        "behavior_prompt_template": node.behavior_prompt_template,
                    }
                    for node in runtime.nodes
                ],
                "task_assignment": dict(runtime.task_assignment),
                "compile_warnings": list(runtime.compile_warnings),
            },
            "task_order_map": {node.id: node.block_index for node in runtime.nodes},
            "task_parse_issues": list(runtime.task_parse_issues),
            "recovery": {
                "status": "none",
                "options": ["manual", "agent_best_effort", "abort_phase"],
                "selected_mode": None,
                "prompt": None,
                "attempts": 0,
            },
        }

    def _materialize_runtime_plan(
        self, plan: PlanSpec
    ) -> tuple[RuntimeExecutionPlan, dict[str, Any]]:
        tasks, parse_issues = parse_plan_tasks(plan)
        runtime, compile_issues = compile_execution_structure(
            plan, tasks, task_parse_issues=parse_issues
        )
        if runtime is None or compile_issues:
            details = [
                {"code": "tasks.block_model.invalid", "message": issue, "path": "tasks"}
                for issue in compile_issues
            ]
            raise ValueError(f"validation_error: {json.dumps(details)}")
        return runtime, self._runtime_metadata(runtime, tasks=tasks)

    def _writeback_target(
        self, source: Path, plan: PlanSpec
    ) -> tuple[str, Path | None]:
        mode = str(self.config.task_writeback_mode or "revision_only")
        if mode == "disabled":
            return mode, None
        if mode == "in_place":
            return mode, source
        filename = versioned_filename(plan.plan_id, "completed")
        return "revision_only", self.paths["plans"] / filename

    def git_runtime_status(self) -> dict[str, Any]:
        return GitWorktreeManager(
            self.workspace_root, "runtime-check"
        ).execution_status()

    def git_repository_status(self) -> dict[str, Any]:
        return GitWorktreeManager(
            self.workspace_root, "runtime-check"
        ).repository_status()

    def require_git_workspace(self) -> None:
        status = self.git_runtime_status()
        if not bool(status.get("ok")):
            raise GitRequiredError(self.workspace_root)

    def require_git_repository(self) -> None:
        status = self.git_repository_status()
        if not bool(status.get("ok")):
            raise GitRequiredError(self.workspace_root)

    def run_start_preflight(self) -> dict[str, Any]:
        recoverable_runs = self.list_recoverable_runs()
        stale = self.stale_artifact_report(max_age_hours=0)
        stale_worktrees = (
            stale.get("stale_worktrees", [])
            if isinstance(stale.get("stale_worktrees"), list)
            else []
        )
        stale_branches = (
            stale.get("stale_branches", [])
            if isinstance(stale.get("stale_branches"), list)
            else []
        )
        blocking_reasons: list[str] = []
        issues: list[dict[str, Any]] = []
        if recoverable_runs:
            blocking_reasons.append(
                "recoverable runs are still present in this workspace"
            )
            issues.append(
                {
                    "code": "stale_recovery_state_present",
                    "message": "recoverable runs exist and must be resolved before starting a new run",
                    "run_ids": list(recoverable_runs),
                }
            )
        if stale_worktrees or stale_branches:
            blocking_reasons.append(
                "stale managed git artifacts are still present in this workspace"
            )
            issues.append(
                {
                    "code": "stale_recovery_state_present",
                    "message": "stale managed worktrees or branches were detected",
                    "stale_worktrees": stale_worktrees,
                    "stale_branches": stale_branches,
                }
            )
        detail = (
            "workspace has unresolved recoverable runs or stale managed artifacts"
            if blocking_reasons
            else "workspace is clear for a new run"
        )
        next_commands = [
            "uv run ralphite history --workspace . --output table",
            "uv run ralphite recover --workspace . --output table",
        ]
        return {
            "ok": len(blocking_reasons) == 0,
            "reason": (
                "stale_recovery_state_present" if blocking_reasons else "ok"
            ),
            "detail": detail,
            "blocking_reasons": blocking_reasons,
            "recoverable_runs": recoverable_runs,
            "stale_artifacts": stale,
            "next_commands": next_commands,
            "issues": issues,
        }

    def collect_requirements(
        self, plan_ref: str | None = None, plan_content: str | None = None
    ) -> dict[str, list[str]]:
        path: Path | None = None
        if plan_content is None:
            path = self._resolve_plan_path(plan_ref)
            plan_content = path.read_text(encoding="utf-8")
        plan = parse_plan_yaml(
            plan_content,
            workspace_root=self.workspace_root,
            plan_path=str(path) if path is not None else None,
        )
        tools = sorted(
            {
                item
                for profile in plan.agents
                for item in profile.tools_allow
                if item.startswith("tool:")
            }
        )
        mcps = sorted(
            {
                item
                for profile in plan.agents
                for item in profile.tools_allow
                if item.startswith("mcp:")
            }
        )
        return {"tools": tools, "mcps": mcps}

    def _persist_runtime_state(self, handle: RuntimeHandle, status: str) -> None:
        self.state_manager.persist_runtime_state(handle, status)

    def _checkpoint(self, handle: RuntimeHandle, status: str = "running") -> None:
        self.state_manager.checkpoint(handle, status)

    def start_run(
        self,
        *,
        plan_ref: str | None = None,
        plan_content: str | None = None,
        backend_override: str | None = None,
        model_override: str | None = None,
        reasoning_effort_override: str | None = None,
        permission_snapshot: dict[str, list[str]] | None = None,
        metadata: dict[str, Any] | None = None,
        require_clean_git: bool = True,
        first_failure_recovery: str | None = None,
    ) -> str:
        if require_clean_git:
            self.require_git_workspace()
        else:
            self.require_git_repository()
        start_preflight = self.run_start_preflight()
        if not bool(start_preflight.get("ok")):
            raise RunStartBlockedError(start_preflight)
        compile_started = time.perf_counter()
        if plan_content is None:
            source_path = self._resolve_plan_path(plan_ref)
            content = source_path.read_text(encoding="utf-8")
        else:
            source_path = self.paths["plans"] / f"inline-{uuid4().hex[:12]}.yaml"
            source_path.write_text(plan_content, encoding="utf-8")
            content = plan_content

        valid, issues, summary = validate_plan_content(
            content,
            workspace_root=self.workspace_root,
            plan_path=str(source_path),
        )
        if not valid:
            raise ValueError(f"validation_error: {json.dumps(issues)}")

        plan_document, defaults_meta = parse_plan_with_defaults(
            content,
            workspace_root=self.workspace_root,
            plan_path=str(source_path),
        )
        compile_plan(plan_document)
        runtime, runtime_meta = self._materialize_runtime_plan(plan_document)
        compile_seconds = round(max(0.0, time.perf_counter() - compile_started), 3)

        profile_map = {profile.id: profile for profile in plan_document.agents}
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

        run_id = str(uuid4())
        snapshot = permission_snapshot or self.default_permission_snapshot()
        execution_backend = (
            (backend_override or self.config.default_backend or "codex").strip().lower()
        )
        if execution_backend not in {"codex", "cursor"}:
            execution_backend = "codex"
        execution_model = (
            model_override or self.config.default_model or "gpt-5.3-codex"
        ).strip() or "gpt-5.3-codex"
        execution_reasoning_effort = (
            (
                reasoning_effort_override
                or self.config.default_reasoning_effort
                or "medium"
            )
            .strip()
            .lower()
        )
        if execution_reasoning_effort not in {"low", "medium", "high"}:
            execution_reasoning_effort = "medium"
        first_failure_recovery_mode = (
            str(first_failure_recovery or "none").strip().lower() or "none"
        )
        if first_failure_recovery_mode not in {"none", "agent_best_effort"}:
            first_failure_recovery_mode = "none"
        git_manager = GitWorktreeManager(self.workspace_root, run_id)
        run = RunViewState(
            id=run_id,
            plan_path=str(source_path),
            status="queued",
            started_at=datetime.now(timezone.utc).isoformat(),
            nodes=nodes,
            metadata={
                "plan": summary,
                "defaults_resolution": defaults_meta,
                "compile_seconds": compile_seconds,
                "permission_snapshot": snapshot,
                "task_writeback_mode": self.config.task_writeback_mode,
                "execution_defaults": {
                    "backend": execution_backend,
                    "model": execution_model,
                    "reasoning_effort": execution_reasoning_effort,
                    "cursor_command": self.config.cursor_command,
                },
                "first_failure_recovery": first_failure_recovery_mode,
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
        handle.thread = threading.Thread(
            target=self._execute_run, args=(handle,), daemon=False
        )
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

    def list_history(
        self, limit: int = 20, query: str | None = None
    ) -> list[RunViewState]:
        return self.history.list(limit=limit, query=query)

    def pause_run(self, run_id: str) -> bool:
        handle = self.active.get(run_id)
        if not handle or handle.finished_event.is_set():
            return False
        handle.pause_event.set()
        handle.run.status = "paused"
        self._emit(
            handle,
            stage="orchestrator",
            event="RUN_PAUSED",
            level="warn",
            message="run paused",
        )
        self._persist_runtime_state(handle, "paused")
        return True

    def resume_run(self, run_id: str) -> bool:
        handle = self.active.get(run_id)
        if (
            handle
            and not handle.finished_event.is_set()
            and handle.thread
            and handle.thread.is_alive()
        ):
            handle.pause_event.clear()
            handle.run.status = "running"
            self._emit(
                handle,
                stage="orchestrator",
                event="RUN_RESUMED",
                level="info",
                message="run resumed",
            )
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
            require_clean_git=False,
            first_failure_recovery=str(
                previous.metadata.get("first_failure_recovery") or "none"
            )
            if isinstance(previous.metadata, dict)
            else "none",
        )

    def list_recoverable_runs(self) -> list[str]:
        return self.state_manager.list_recoverable_runs()

    def stale_artifact_report(
        self, max_age_hours: int = 24
    ) -> dict[str, list[dict[str, Any]]]:
        active_by_state: list[str] = []
        for run_id in self.run_store.list_run_ids():
            state = self.run_store.load_state(run_id)
            if not state:
                continue
            if state.status in {
                "running",
                "checkpointing",
                "paused",
                "paused_recovery_required",
                "recovering",
            }:
                active_by_state.append(run_id)
        active_run_ids = sorted(set(active_by_state + self.list_active_run_ids()))
        manager = GitWorktreeManager(self.workspace_root, "doctor")
        return manager.detect_stale_artifacts(
            active_run_ids=active_run_ids, max_age_hours=max_age_hours
        )

    def recover_run(self, run_id: str) -> bool:
        self.require_git_repository()
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
        plan_document, defaults_meta = parse_plan_with_defaults(
            plan_content,
            workspace_root=self.workspace_root,
            plan_path=run.plan_path,
        )
        compile_plan(plan_document)
        runtime, runtime_meta = self._materialize_runtime_plan(plan_document)
        run.metadata.setdefault("defaults_resolution", defaults_meta)

        run.metadata.setdefault("plan_version", runtime_meta.get("plan_version", 5))

        run.metadata.setdefault("parallel_limit", runtime_meta.get("parallel_limit", 1))
        run.metadata.setdefault("lane_map", runtime_meta.get("lane_map", {}))
        run.metadata.setdefault("phase_map", runtime_meta.get("phase_map", {}))
        run.metadata.setdefault("role_map", runtime_meta.get("role_map", {}))
        run.metadata.setdefault("cell_map", runtime_meta.get("cell_map", {}))
        run.metadata.setdefault("team_map", runtime_meta.get("team_map", {}))
        run.metadata.setdefault("behavior_map", runtime_meta.get("behavior_map", {}))
        run.metadata.setdefault("phase_nodes", runtime_meta.get("phase_nodes", {}))
        run.metadata.setdefault(
            "task_surface_map", runtime_meta.get("task_surface_map", {})
        )
        run.metadata.setdefault(
            "node_surface_map", runtime_meta.get("node_surface_map", {})
        )
        run.metadata.setdefault("task_blocks", runtime_meta.get("task_blocks", []))
        run.metadata.setdefault(
            "resolved_execution", runtime_meta.get("resolved_execution", {})
        )
        run.metadata.setdefault(
            "task_order_map", runtime_meta.get("task_order_map", {})
        )
        run.metadata.setdefault(
            "task_parse_issues", runtime_meta.get("task_parse_issues", [])
        )
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
        run.metadata.setdefault(
            "git_state",
            GitWorktreeManager(self.workspace_root, run_id).bootstrap_state(),
        )
        run.metadata.setdefault("first_failure_recovery", "none")

        snapshot = run.metadata.get("permission_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = self.default_permission_snapshot()

        profile_map = {profile.id: profile for profile in plan_document.agents}
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
        self._persist_runtime_state(
            handle,
            "paused_recovery_required"
            if run.status == "paused_recovery_required"
            else "paused",
        )
        return True

    def _file_has_conflict_markers(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            return False
        return "<<<<<<< " in text and "=======" in text and ">>>>>>> " in text

    def recovery_preflight(self, run_id: str) -> dict[str, Any]:
        if run_id not in self.active and not self.recover_run(run_id):
            return {
                "ok": False,
                "checks": [
                    {
                        "name": "run_exists",
                        "ok": False,
                        "detail": "run not found or unrecoverable",
                    }
                ],
                "blocking_reasons": ["run not found or unrecoverable"],
                "conflict_files": [],
                "next_commands": [],
            }

        handle = self.active[run_id]
        run = handle.run
        recovery = run.metadata.setdefault("recovery", {})
        details = (
            recovery.get("details") if isinstance(recovery.get("details"), dict) else {}
        )
        selected_mode = str(recovery.get("selected_mode") or "")
        selected_prompt = str(recovery.get("prompt") or "").strip()
        checks: list[dict[str, Any]] = []
        blocking_reasons: list[str] = []

        if run.status not in {"paused", "paused_recovery_required"}:
            checks.append(
                {
                    "name": "status_paused",
                    "ok": False,
                    "detail": f"run status is {run.status}",
                }
            )
            blocking_reasons.append(
                f"run must be paused for recovery (current: {run.status})"
            )
        else:
            checks.append({"name": "status_paused", "ok": True, "detail": run.status})

        mode_ok = selected_mode in {"manual", "agent_best_effort", "abort_phase"}
        checks.append(
            {
                "name": "selected_mode",
                "ok": mode_ok,
                "detail": selected_mode or "not selected",
            }
        )
        if not mode_ok:
            blocking_reasons.append("select a valid recovery mode before resume")

        prompt_ok = not (selected_mode == "agent_best_effort" and not selected_prompt)
        checks.append(
            {
                "name": "agent_prompt",
                "ok": prompt_ok,
                "detail": "present" if selected_prompt else "missing",
            }
        )
        if not prompt_ok:
            blocking_reasons.append(
                "agent_best_effort mode requires a non-empty prompt"
            )

        lock_payload = self.run_store.read_lock(run_id)
        lock_ok = lock_payload is None or self.run_store.lock_is_stale(run_id)
        checks.append(
            {
                "name": "lock_available",
                "ok": lock_ok,
                "detail": "stale_or_absent" if lock_ok else "lock currently held",
            }
        )
        if not lock_ok:
            blocking_reasons.append(
                "run lock is currently held by another active process"
            )

        worktree = (
            Path(str(details.get("worktree"))) if details.get("worktree") else None
        )
        worktree_ok = worktree is None or worktree.exists()
        checks.append(
            {
                "name": "worktree_available",
                "ok": worktree_ok,
                "detail": str(worktree) if worktree else "none",
            }
        )
        if not worktree_ok:
            blocking_reasons.append(f"recovery worktree is unavailable: {worktree}")

        conflict_files = (
            details.get("conflict_files")
            if isinstance(details.get("conflict_files"), list)
            else []
        )
        unresolved_conflicts: list[str] = []
        if worktree and worktree.exists() and conflict_files:
            for relative in conflict_files:
                path = worktree / str(relative)
                if path.exists() and self._file_has_conflict_markers(path):
                    unresolved_conflicts.append(str(relative))
        unresolved_ok = len(unresolved_conflicts) == 0
        checks.append(
            {
                "name": "conflicts_resolved",
                "ok": unresolved_ok,
                "detail": "resolved"
                if unresolved_ok
                else f"{len(unresolved_conflicts)} unresolved file(s)",
            }
        )
        if not unresolved_ok:
            blocking_reasons.append(
                "unresolved merge markers remain in recovery worktree"
            )

        next_commands = (
            details.get("next_commands")
            if isinstance(details.get("next_commands"), list)
            else []
        )
        return {
            "ok": len(blocking_reasons) == 0,
            "checks": checks,
            "blocking_reasons": blocking_reasons,
            "conflict_files": conflict_files,
            "unresolved_conflict_files": unresolved_conflicts,
            "next_commands": next_commands,
        }

    def set_recovery_mode(
        self, run_id: str, mode: str, prompt: str | None = None
    ) -> bool:
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
        self.require_git_workspace()
        if run_id not in self.active and not self.recover_run(run_id):
            return False

        handle = self.active[run_id]
        if handle.thread and handle.thread.is_alive():
            return False

        recovery = handle.run.metadata.get("recovery", {})
        if handle.run.status == "paused_recovery_required":
            selected = recovery.get("selected_mode")
            if selected == "abort_phase":
                recovery["status"] = "aborted"
                handle.run.status = "failed"
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="RECOVERY_ABORTED",
                    level="error",
                    message="recovery aborted by user",
                )
                self._finalize_terminal_run(
                    handle, GitWorktreeManager(self.workspace_root, run_id)
                )
                return True
            if selected not in {"manual", "agent_best_effort"}:
                return False
            preflight = self.recovery_preflight(run_id)
            if not bool(preflight.get("ok")):
                recovery["status"] = "preflight_failed"
                if preflight.get("unresolved_conflict_files"):
                    self._record_interruption_reason(
                        handle.run, "recovery_conflict_files_present"
                    )
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="RECOVERY_PREFLIGHT_FAILED",
                    level="error",
                    message="recovery preflight failed",
                    meta=preflight,
                )
                self._persist_runtime_state(handle, "paused_recovery_required")
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
        recovery["status"] = "resumed"
        handle.run.status = "running"
        self._emit(
            handle,
            stage="orchestrator",
            event="RUN_RESUME_FROM_CHECKPOINT",
            level="info",
            message="run resumed from checkpoint",
        )
        if handle.run.metadata.get("recovery", {}).get("selected_mode"):
            self._emit(
                handle,
                stage="orchestrator",
                event="RECOVERY_RESUMED",
                level="info",
                message="recovery resume started",
                meta={
                    "mode": handle.run.metadata.get("recovery", {}).get("selected_mode")
                },
            )
        self._persist_runtime_state(handle, "running")
        handle.thread = threading.Thread(
            target=self._execute_run, args=(handle,), daemon=False
        )
        handle.thread.start()
        return True

    def wait_for_run(self, run_id: str, timeout: float | None = None) -> bool:
        handle = self.active.get(run_id)
        if not handle:
            return False
        return handle.finished_event.wait(timeout=timeout)

    def stream_events(
        self, run_id: str, after_seq: int = 0
    ) -> Generator[dict[str, Any], None, None]:
        yield from self.event_logger.stream_events(run_id, after_seq)

    def poll_events(self, run_id: str) -> list[dict[str, Any]]:
        return self.event_logger.poll_events(run_id)

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
        self.event_logger.emit(
            handle,
            stage=stage,
            event=event,
            level=level,
            message=message,
            group=group,
            task_id=task_id,
            meta=meta,
        )

    def _record_interruption_reason(self, run: RunViewState, reason: str) -> None:
        normalized = str(reason or "").strip()
        if not normalized:
            return
        reasons = run.metadata.setdefault("interruption_reasons", [])
        if isinstance(reasons, list):
            reasons.append(normalized)

    def _build_auto_recovery_prompt(
        self,
        *,
        node: RuntimeNodeSpec,
        details: dict[str, Any],
        worktree: Path,
    ) -> str:
        overlap_files = details.get("overlap_files")
        conflict_files = details.get("current_run_conflict_files") or details.get(
            "conflict_files"
        )
        lines = [
            f"Recover the blocked Ralphite merge/integration step for phase '{node.phase}'.",
            "Preserve every existing local user change.",
            "Do not run git reset, git checkout --, git clean -fd, or any destructive revert.",
            "If files contain conflict markers, resolve them conservatively and commit the resolution.",
            "If the workspace is already consistent and committed, make no unnecessary changes.",
            f"Affected worktree: {worktree}",
        ]
        if isinstance(overlap_files, list) and overlap_files:
            lines.append(
                f"Overlapping local files: {', '.join(str(item) for item in overlap_files[:12])}"
            )
        if isinstance(conflict_files, list) and conflict_files:
            lines.append(
                f"Conflict files: {', '.join(str(item) for item in conflict_files[:12])}"
            )
        error = str(details.get("error") or "").strip()
        if error:
            lines.append(f"Merge error: {error}")
        lines.append(
            "End by printing a concise summary of what you changed and whether the worktree is ready for merge retry."
        )
        return "\n".join(lines)

    def _attempt_inline_auto_recovery(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        details: dict[str, Any],
        git_manager: GitWorktreeManager,
    ) -> tuple[str, dict[str, Any]]:
        recovery_mode = str(
            handle.run.metadata.get("first_failure_recovery") or "none"
        ).strip()
        recovery = handle.run.metadata.setdefault("recovery", {})
        if recovery_mode != "agent_best_effort":
            return "recovery_required", details
        if bool(recovery.get("auto_attempted")):
            return "recovery_required", details

        reason = str(details.get("reason") or "").strip()
        worktree_raw = str(details.get("worktree") or self.workspace_root).strip()
        worktree = Path(worktree_raw).expanduser().resolve()
        prompt = self._build_auto_recovery_prompt(
            node=node, details=details, worktree=worktree
        )
        recovery.update(
            {
                "selected_mode": "agent_best_effort",
                "prompt": prompt,
                "auto_attempted": True,
                "auto_attempt_status": "started",
            }
        )
        details["auto_recovery"] = {
            "mode": "agent_best_effort",
            "status": "started",
        }

        if reason == "base_integration_blocked_by_local_changes":
            recovery["auto_attempt_status"] = "unsafe_skipped"
            details["auto_recovery"] = {
                "mode": "agent_best_effort",
                "status": "unsafe_skipped",
                "reason": "primary workspace has overlapping local edits",
            }
            self._emit(
                handle,
                stage="orchestrator",
                event="RECOVERY_AUTO_SKIPPED",
                level="warn",
                message="automatic recovery skipped because overlapping local edits were detected in the primary workspace",
                group=node.phase,
                task_id=node.id,
                meta={"reason": reason, "worktree": str(worktree)},
            )
            return "recovery_required", details

        profile = handle.profile_map.get(node.agent_profile_id)
        if not profile:
            recovery["auto_attempt_status"] = "failed"
            details["auto_recovery"] = {
                "mode": "agent_best_effort",
                "status": "failed",
                "reason": "orchestrator profile missing",
            }
            return "recovery_required", details

        self._emit(
            handle,
            stage="orchestrator",
            event="RECOVERY_AUTO_STARTED",
            level="warn",
            message="automatic best-effort recovery started",
            group=node.phase,
            task_id=node.id,
            meta={"reason": reason, "worktree": str(worktree)},
        )
        synthetic_node = RuntimeNodeSpec(
            id=f"{node.id}::auto-recovery",
            kind=node.kind,
            group=node.group,
            depends_on=[],
            task=prompt,
            agent_profile_id=node.agent_profile_id,
            role="orchestrator",
            phase=node.phase,
            lane=node.lane,
            cell_id=node.cell_id,
            team=node.team,
            behavior_id=node.behavior_id,
            behavior_kind=node.behavior_kind,
            source_task_id=node.source_task_id,
            block_index=node.block_index,
        )
        agent_ok, agent_result = self._execute_agent(
            handle,
            synthetic_node,
            profile,
            handle.permission_snapshot,
            worktree=worktree,
        )
        if not agent_ok:
            recovery["auto_attempt_status"] = "failed"
            details["auto_recovery"] = {
                "mode": "agent_best_effort",
                "status": "failed",
                "error": agent_result,
            }
            self._emit(
                handle,
                stage="orchestrator",
                event="RECOVERY_AUTO_FAILED",
                level="error",
                message="automatic best-effort recovery agent failed",
                group=node.phase,
                task_id=node.id,
                meta=agent_result,
            )
            return "recovery_required", details

        status, merge_meta = git_manager.integrate_phase(
            handle.run.metadata.setdefault("git_state", {}),
            node.phase,
            recovery_mode="agent_best_effort",
            recovery_prompt=prompt,
        )
        if status == "success":
            recovery["auto_attempt_status"] = "succeeded"
            details["auto_recovery"] = {
                "mode": "agent_best_effort",
                "status": "succeeded",
            }
            self._emit(
                handle,
                stage="orchestrator",
                event="RECOVERY_AUTO_DONE",
                level="info",
                message="automatic best-effort recovery succeeded",
                group=node.phase,
                task_id=node.id,
                meta={"status": status},
            )
            return "success", {
                "auto_recovery": {
                    "mode": "agent_best_effort",
                    "status": "succeeded",
                    "agent_result": agent_result,
                },
                "integration": merge_meta,
            }

        recovery["auto_attempt_status"] = "failed"
        merge_meta["auto_recovery"] = {
            "mode": "agent_best_effort",
            "status": "failed",
        }
        self._emit(
            handle,
            stage="orchestrator",
            event="RECOVERY_AUTO_DONE",
            level="error",
            message="automatic best-effort recovery did not clear the integration failure",
            group=node.phase,
            task_id=node.id,
            meta={"status": status, "reason": merge_meta.get("reason")},
        )
        if status == "failed":
            return "failure", {"reason": "runtime_error", **merge_meta}
        return "recovery_required", merge_meta

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

    def _resolve_execution_defaults(
        self, handle: RuntimeHandle, profile: AgentSpec
    ) -> tuple[str, str, str, str]:
        defaults = (
            handle.run.metadata.get("execution_defaults")
            if isinstance(handle.run.metadata.get("execution_defaults"), dict)
            else {}
        )
        backend_raw = (
            str(
                defaults.get("backend")
                or profile.provider.value
                or self.config.default_backend
                or "codex"
            )
            .strip()
            .lower()
        )
        if backend_raw not in {"codex", "cursor"}:
            backend_raw = "codex"

        model_raw = str(
            defaults.get("model")
            or profile.model
            or self.config.default_model
            or "gpt-5.3-codex"
        ).strip()
        model = model_raw or "gpt-5.3-codex"

        reasoning_raw = (
            str(
                defaults.get("reasoning_effort")
                or profile.reasoning_effort.value
                or self.config.default_reasoning_effort
                or "medium"
            )
            .strip()
            .lower()
        )
        reasoning_effort = (
            reasoning_raw if reasoning_raw in {"low", "medium", "high"} else "medium"
        )
        cursor_command = (
            str(
                defaults.get("cursor_command") or self.config.cursor_command or "agent"
            ).strip()
            or "agent"
        )
        return backend_raw, model, reasoning_effort, cursor_command

    def _execute_agent(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        profile: AgentSpec,
        snapshot: dict[str, list[str]],
        *,
        worktree: Path,
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

        backend, model, reasoning_effort, cursor_command = (
            self._resolve_execution_defaults(handle, profile)
        )
        try:
            prompt = build_node_prompt(
                node,
                worktree=worktree,
                permission_snapshot=snapshot,
                plan_id=handle.plan.plan_id,
                plan_name=handle.plan.name,
                agent_id=profile.id,
                agent_role=profile.role.value,
                system_prompt=profile.system_prompt,
                behavior_prompt_template=node.behavior_prompt_template,
            )
        except ValueError as exc:
            return False, {
                "reason": "defaults.placeholder_invalid",
                "error": str(exc),
                "agent_id": profile.id,
                "role": node.role,
            }
        ok, result = execute_headless_agent(
            config=BackendExecutionConfig(
                backend=backend,
                model=model,
                reasoning_effort=reasoning_effort,
                cursor_command=cursor_command,
                timeout_seconds=max(
                    60, int(handle.plan.constraints.max_runtime_seconds)
                ),
            ),
            prompt=prompt,
            worktree=worktree,
        )
        if not ok:
            return False, result
        return True, {
            **result,
            "agent_id": profile.id,
            "provider": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "role": node.role,
            "phase": node.phase,
            "lane": node.lane,
        }

    def _emit_node_started(self, handle: RuntimeHandle, node: RuntimeNodeSpec) -> None:
        self.event_logger.emit_node_started(handle, node)

    def _emit_node_completed(
        self, handle: RuntimeHandle, node: RuntimeNodeSpec, success: bool
    ) -> None:
        self.event_logger.emit_node_completed(handle, node, success)

    def _start_node_execution(
        self, handle: RuntimeHandle, node: RuntimeNodeSpec
    ) -> None:
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
        self._emit_node_started(handle, node)

    def _handle_recovery_required(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        details: dict[str, Any],
    ) -> None:
        if not isinstance(details.get("conflict_files"), list):
            details["conflict_files"] = []
        if not isinstance(details.get("next_commands"), list):
            details["next_commands"] = [
                "Resolve merge conflicts in the reported worktree.",
                "Set recovery mode and resume.",
            ]

        recovery = handle.run.metadata.setdefault("recovery", {})
        recovery["status"] = "required"
        recovery["phase"] = node.phase
        recovery["details"] = details
        recovery["attempts"] = int(recovery.get("attempts") or 0) + 1
        self._record_interruption_reason(
            handle.run, str(details.get("reason") or "runtime_error")
        )

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
            self._emit_node_completed(handle, node, True)
            return

        reason = str(result.get("reason", "runtime_error") or "runtime_error")
        max_retries = max(0, int(handle.plan.constraints.max_retries_per_node or 0))
        non_retryable = {
            "acceptance_artifact_missing",
            "acceptance_artifact_out_of_bounds",
            "worker_merge_conflict",
            "base_merge_conflict",
            "simulated_conflict",
            "backend_binary_missing",
            "backend_model_unsupported",
            "backend_auth_failed",
            "backend_output_malformed",
            "backend_out_of_worktree_claim",
            "backend_worktree_missing",
            "defaults.placeholder_invalid",
        }
        if reason not in non_retryable and rec.attempt_count <= max_retries:
            rec.status = "queued"
            rec.result = result
            handle.run.retry_count += 1
            self._emit(
                handle,
                stage="task",
                event="NODE_RETRY_SCHEDULED",
                level="warn",
                message="retry scheduled for node",
                group=node.group,
                task_id=node.id,
                meta={
                    "reason": reason,
                    "attempt": rec.attempt_count,
                    "max_retries_per_node": max_retries,
                },
            )
            return

        rec.status = "failed"
        advice = classify_failure(reason)
        enriched_result = dict(result)
        enriched_result.setdefault("reason", reason)
        enriched_result.setdefault("failure_title", advice.title)
        enriched_result.setdefault("next_action", advice.next_action)
        enriched_result.setdefault("command_hint", advice.command_hint)
        rec.result = enriched_result
        self._emit(
            handle,
            stage="task",
            event="NODE_RESULT",
            level="error",
            message=f"{advice.title}: {advice.message}",
            group=node.group,
            task_id=node.id,
            meta={
                "status": rec.status,
                "reason": reason,
                "next_action": advice.next_action,
                "command_hint": advice.command_hint,
            },
        )
        self._emit_node_completed(handle, node, False)

        if fail_fast:
            for queued in handle.run.nodes.values():
                if queued.status == "queued":
                    queued.status = "blocked"

    def _build_run_metrics(
        self,
        run: RunViewState,
        *,
        execution_seconds: float,
        cleanup_seconds: float,
        total_seconds: float,
    ) -> RunMetrics:
        node_status_counts: dict[str, int] = {}
        node_role_counts: dict[str, int] = {}
        failure_reason_counts: dict[str, int] = {}
        interruption_reason_counts: dict[str, int] = {}
        role_map = (
            run.metadata.get("role_map", {})
            if isinstance(run.metadata.get("role_map"), dict)
            else {}
        )

        for node_id, node in run.nodes.items():
            node_status_counts[node.status] = (
                int(node_status_counts.get(node.status, 0)) + 1
            )
            role = str(role_map.get(node_id) or "unknown")
            node_role_counts[role] = int(node_role_counts.get(role, 0)) + 1
            if node.status == "failed" and isinstance(node.result, dict):
                reason = str(node.result.get("reason") or "runtime_error")
                failure_reason_counts[reason] = (
                    int(failure_reason_counts.get(reason, 0)) + 1
                )
        interruption_reasons = (
            run.metadata.get("interruption_reasons", [])
            if isinstance(run.metadata.get("interruption_reasons"), list)
            else []
        )
        for reason in interruption_reasons:
            normalized = str(reason or "").strip()
            if not normalized:
                continue
            interruption_reason_counts[normalized] = (
                int(interruption_reason_counts.get(normalized, 0)) + 1
            )

        return RunMetrics(
            compile_seconds=round(
                float(run.metadata.get("compile_seconds", 0.0) or 0.0), 3
            ),
            execution_seconds=round(max(0.0, execution_seconds), 3),
            cleanup_seconds=round(max(0.0, cleanup_seconds), 3),
            total_seconds=round(max(0.0, total_seconds), 3),
            node_status_counts=node_status_counts,
            node_role_counts=node_role_counts,
            failure_reason_counts=failure_reason_counts,
            interruption_reason_counts=interruption_reason_counts,
            retry_count=int(run.retry_count or 0),
        )

    def _write_artifacts(self, run: RunViewState) -> ArtifactIndex:
        artifacts_dir = self.paths["artifacts"] / run.id
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        metrics_payload = (
            run.metadata.get("run_metrics", {})
            if isinstance(run.metadata.get("run_metrics"), dict)
            else {}
        )

        report_path = artifacts_dir / "final_report.md"
        metrics_path = artifacts_dir / "run_metrics.json"
        bundle_path = artifacts_dir / "machine_bundle.json"
        report = build_final_report(
            run,
            artifact_paths={
                "final_report": str(report_path),
                "run_metrics": str(metrics_path),
                "machine_bundle": str(bundle_path),
            },
            run_state_paths={
                "run_state": str(self.paths["runs"] / run.id / "run_state.json"),
                "checkpoint": str(self.paths["runs"] / run.id / "checkpoint.json"),
                "event_log": str(self.paths["runs"] / run.id / "event_log.ndjson"),
            },
        )
        report_path.write_text(report, encoding="utf-8")

        metrics_path.write_text(json.dumps(metrics_payload, indent=2), encoding="utf-8")

        bundle = {
            "run_id": run.id,
            "status": run.status,
            "plan_path": run.plan_path,
            "retry_count": run.retry_count,
            "nodes": {k: v.model_dump(mode="json") for k, v in run.nodes.items()},
            "metadata": run.metadata,
            "metrics": metrics_payload,
        }
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

        items = [
            {"id": "final_report", "path": str(report_path), "format": "markdown"},
            {"id": "run_metrics", "path": str(metrics_path), "format": "json"},
            {"id": "machine_bundle", "path": str(bundle_path), "format": "json"},
        ]
        run.artifacts = items
        return ArtifactIndex(
            run_id=run.id, artifacts_dir=str(artifacts_dir), items=items
        )

    def _node_surfaces(
        self, handle: RuntimeHandle, node: RuntimeNodeSpec
    ) -> set[str]:
        node_surface_map = (
            handle.run.metadata.get("node_surface_map", {})
            if isinstance(handle.run.metadata.get("node_surface_map"), dict)
            else {}
        )
        surfaces = node_surface_map.get(node.id, [])
        if not isinstance(surfaces, list):
            return set()
        return {str(item).strip().lower() for item in surfaces if str(item).strip()}

    def _high_overlap_surfaces(
        self, handle: RuntimeHandle, nodes: list[RuntimeNodeSpec]
    ) -> list[str]:
        if len(nodes) < 2:
            return []
        token_counts: dict[str, int] = {}
        for node in nodes:
            for token in self._node_surfaces(handle, node):
                token_counts[token] = int(token_counts.get(token, 0)) + 1
        return sorted(token for token, count in token_counts.items() if count > 1)

    def _cleanup_completed_phases(
        self, handle: RuntimeHandle, git_manager: GitWorktreeManager
    ) -> None:
        phase_done = (
            handle.run.metadata.get("phase_done", [])
            if isinstance(handle.run.metadata.get("phase_done"), list)
            else []
        )
        cleaned = handle.run.metadata.setdefault("phase_cleanup_done", [])
        if not isinstance(cleaned, list):
            cleaned = []
            handle.run.metadata["phase_cleanup_done"] = cleaned
        for phase in phase_done:
            if phase in cleaned:
                continue
            phase_node_ids = (
                handle.run.metadata.get("phase_nodes", {}).get(phase, [])
                if isinstance(handle.run.metadata.get("phase_nodes"), dict)
                else []
            )
            if not isinstance(phase_node_ids, list) or not phase_node_ids:
                continue
            statuses = [
                handle.run.nodes[node_id].status
                for node_id in phase_node_ids
                if node_id in handle.run.nodes
            ]
            if not statuses or not all(status == "succeeded" for status in statuses):
                continue
            cleanup_notes = git_manager.cleanup_phase(
                handle.run.metadata.setdefault("git_state", {}),
                str(phase),
            )
            if cleanup_notes:
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="PHASE_CLEANUP_DONE",
                    level="info",
                    message="phase git artifacts cleaned after successful phase completion",
                    group=str(phase),
                    meta={"items": cleanup_notes},
                )
            cleaned.append(str(phase))

    def _choose_batch(
        self, handle: RuntimeHandle, ready_nodes: list[RuntimeNodeSpec]
    ) -> list[RuntimeNodeSpec]:
        if not ready_nodes:
            return []

        ready_nodes = sorted(ready_nodes, key=lambda node: (node.block_index, node.id))
        first_block = ready_nodes[0].block_index
        same_block = [node for node in ready_nodes if node.block_index == first_block]
        if not same_block:
            return []

        parallel_limit = max(1, int(handle.runtime.parallel_limit or 1))
        worker_same_block = [node for node in same_block if node.role == "worker"]
        parallel_ready = [node for node in worker_same_block if node.lane == "parallel"]
        if parallel_ready:
            return parallel_ready[:parallel_limit]

        # Branched lanes can run concurrently even if each lane segment is sequential.
        if worker_same_block and len({node.lane for node in worker_same_block}) > 1:
            overlap_tokens = self._high_overlap_surfaces(handle, worker_same_block)
            if overlap_tokens:
                serialized = handle.run.metadata.setdefault(
                    "serialized_overlap_blocks", []
                )
                already_recorded = any(
                    isinstance(item, dict)
                    and int(item.get("block_index", -1)) == int(first_block)
                    for item in serialized
                )
                if not already_recorded:
                    serialized.append(
                        {
                            "block_index": first_block,
                            "phase": worker_same_block[0].phase,
                            "lanes": sorted(
                                {node.lane for node in worker_same_block if node.lane}
                            ),
                            "surfaces": overlap_tokens,
                        }
                    )
                    self._emit(
                        handle,
                        stage="orchestrator",
                        event="DISPATCH_SERIALIZED_FOR_OVERLAP",
                        level="warn",
                        message="high-overlap multi-lane block serialized",
                        group=worker_same_block[0].phase,
                        meta={
                            "block_index": first_block,
                            "lanes": sorted(
                                {node.lane for node in worker_same_block if node.lane}
                            ),
                            "surfaces": overlap_tokens,
                        },
                    )
                return [worker_same_block[0]]
            return worker_same_block[:parallel_limit]

        return [same_block[0]]

    def _successful_task_ids(self, handle: RuntimeHandle) -> list[str]:
        task_ids: list[str] = []
        for node in handle.runtime.nodes:
            if node.role != "worker" or not node.source_task_id:
                continue
            node_state = handle.run.nodes.get(node.id)
            if node_state and node_state.status == "succeeded":
                task_ids.append(node.source_task_id)
        return sorted(dict.fromkeys(task_ids))

    def _writeback_tasks(
        self,
        *,
        handle: RuntimeHandle,
        git_manager: GitWorktreeManager,
    ) -> dict[str, Any]:
        task_file = Path(handle.run.plan_path)
        task_ids = self._successful_task_ids(handle)
        writeback_mode, writeback_target = self._writeback_target(
            task_file, handle.plan
        )

        if writeback_mode == "disabled":
            return {
                "task_writeback": {
                    "ok": True,
                    "mode": "disabled",
                    "path": str(task_file),
                    "updated": 0,
                    "requested": len(task_ids),
                    "missing": [],
                },
                "task_writeback_commit": {
                    "mode": "disabled",
                    "message": "task write-back disabled by configuration",
                },
            }

        task_writeback = mark_tasks_completed(
            task_file,
            task_ids,
            output_path=None if writeback_mode == "in_place" else writeback_target,
        )
        task_writeback["mode"] = writeback_mode
        if not bool(task_writeback.get("ok")):
            return {
                "error": {"reason": "task_writeback_failed", "details": task_writeback}
            }

        task_writeback_commit: dict[str, Any] = {
            "mode": "noop",
            "message": "no task updates",
        }
        if int(task_writeback.get("updated") or 0) > 0 and writeback_mode == "in_place":
            committed, commit_meta = git_manager.commit_workspace_changes(
                "chore(tasks): mark completed for run",
                paths=[str(task_file)],
            )
            if not committed:
                return {
                    "error": {"reason": "task_writeback_commit_failed", **commit_meta}
                }
            task_writeback_commit = commit_meta
        elif (
            int(task_writeback.get("updated") or 0) > 0
            and writeback_mode == "revision_only"
        ):
            task_writeback_commit = {
                "mode": "revision_only",
                "message": "wrote completed-task revision without committing",
                "paths": [str(writeback_target)] if writeback_target else [],
            }

        return {
            "task_writeback": task_writeback,
            "task_writeback_commit": task_writeback_commit,
        }

    def _resolve_worker_worktree(self, commit_meta: dict[str, Any]) -> Path:
        return self.git_orchestrator.resolve_worker_worktree(commit_meta)

    def _is_worktree_relative_glob(self, path_glob: str) -> bool:
        return self.git_orchestrator.is_worktree_relative_glob(path_glob)

    def _evaluate_acceptance(
        self,
        node: RuntimeNodeSpec,
        commit_meta: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> tuple[bool, dict[str, Any]]:
        acceptance = node.acceptance if isinstance(node.acceptance, dict) else {}
        commands = (
            acceptance.get("commands")
            if isinstance(acceptance.get("commands"), list)
            else []
        )
        required_artifacts = (
            acceptance.get("required_artifacts")
            if isinstance(acceptance.get("required_artifacts"), list)
            else []
        )
        rubric = (
            acceptance.get("rubric")
            if isinstance(acceptance.get("rubric"), list)
            else []
        )
        if not commands and not required_artifacts:
            return True, {"commands": [], "required_artifacts": [], "rubric": rubric}

        worktree = self._resolve_worker_worktree(commit_meta)
        command_results: list[dict[str, Any]] = []
        for command in commands:
            if not isinstance(command, str) or not command.strip():
                continue
            started = time.monotonic()
            try:
                run = subprocess.run(
                    command,
                    cwd=worktree,
                    shell=True,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout_seconds)),
                )
            except subprocess.TimeoutExpired as exc:
                elapsed = max(0.0, time.monotonic() - started)
                return (
                    False,
                    {
                        "reason": "acceptance_command_timeout",
                        "worktree": str(worktree),
                        "failed_command": command,
                        "timeout_seconds": int(timeout_seconds),
                        "elapsed_seconds": round(elapsed, 3),
                        "stdout": exc.stdout or "",
                        "stderr": exc.stderr or "",
                        "commands": command_results,
                        "required_artifacts": [],
                        "rubric": rubric,
                    },
                )
            result = {
                "command": command,
                "exit_code": run.returncode,
                "stdout": run.stdout,
                "stderr": run.stderr,
            }
            command_results.append(result)
            if run.returncode != 0:
                return (
                    False,
                    {
                        "reason": "acceptance_command_failed",
                        "worktree": str(worktree),
                        "failed_command": command,
                        "commands": command_results,
                        "required_artifacts": [],
                        "rubric": rubric,
                    },
                )

        artifact_results: list[dict[str, Any]] = []
        for item in required_artifacts:
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("id") or "artifact")
            path_glob = str(item.get("path_glob") or "").strip()
            fmt = str(item.get("format") or "unknown")
            if path_glob and not self._is_worktree_relative_glob(path_glob):
                return (
                    False,
                    {
                        "reason": "acceptance_artifact_out_of_bounds",
                        "worktree": str(worktree),
                        "artifact": artifact_id,
                        "path_glob": path_glob,
                        "commands": command_results,
                        "required_artifacts": artifact_results,
                        "rubric": rubric,
                    },
                )

            raw_matches = (
                glob.glob(str(worktree / path_glob), recursive=True)
                if path_glob
                else []
            )
            matches: list[str] = []
            for path in raw_matches:
                resolved = Path(path).resolve()
                try:
                    resolved.relative_to(worktree)
                except ValueError:
                    return (
                        False,
                        {
                            "reason": "acceptance_artifact_out_of_bounds",
                            "worktree": str(worktree),
                            "artifact": artifact_id,
                            "path_glob": path_glob,
                            "out_of_bounds_path": str(resolved),
                            "commands": command_results,
                            "required_artifacts": artifact_results,
                            "rubric": rubric,
                        },
                    )
                matches.append(str(resolved))
            artifact_results.append(
                {
                    "id": artifact_id,
                    "format": fmt,
                    "path_glob": path_glob,
                    "matches": matches,
                }
            )
            if not matches:
                return (
                    False,
                    {
                        "reason": "acceptance_artifact_missing",
                        "worktree": str(worktree),
                        "missing_artifact": artifact_id,
                        "commands": command_results,
                        "required_artifacts": artifact_results,
                        "rubric": rubric,
                    },
                )

        return (
            True,
            {
                "commands": command_results,
                "required_artifacts": artifact_results,
                "rubric": rubric,
            },
        )

    def _run_node(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
    ) -> tuple[str, dict[str, Any]]:
        profile = handle.profile_map.get(node.agent_profile_id)
        if not profile:
            return "failure", {
                "reason": "runtime_error",
                "error": f"unknown agent_id {node.agent_profile_id}",
            }

        if node.role == "orchestrator":
            agent_ok, agent_result = self._execute_agent(
                handle,
                node,
                profile,
                handle.permission_snapshot,
                worktree=self.workspace_root,
            )
            if not agent_ok:
                return "failure", agent_result

            if node.behavior_kind == BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION.value:
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
                return "success", {**agent_result, "integration": merge_meta}

            return "success", agent_result

        if node.role == "worker":
            git_manager.prepare_phase(
                handle.run.metadata.setdefault("git_state", {}), node.phase
            )
            worker_info = git_manager.prepare_worker(
                handle.run.metadata.setdefault("git_state", {}),
                node.phase,
                node.id,
            )
            if worker_info.get("prepare_error"):
                return "failure", {
                    "reason": "worktree_prepare_failed",
                    "error": worker_info["prepare_error"],
                }
            worker_worktree_candidate = (
                Path(str(worker_info.get("worktree_path") or self.workspace_root))
                .expanduser()
                .resolve()
            )
            worker_worktree = (
                worker_worktree_candidate
                if worker_worktree_candidate.exists()
                else self.workspace_root
            )
            ok, result = self._execute_agent(
                handle,
                node,
                profile,
                handle.permission_snapshot,
                worktree=worker_worktree,
            )
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
            acceptance_ok, acceptance_result = self._evaluate_acceptance(
                node,
                commit_meta,
                timeout_seconds=int(handle.plan.constraints.acceptance_timeout_seconds),
            )
            if not acceptance_ok:
                return "failure", acceptance_result
            return "success", {
                **result,
                "worktree": commit_meta,
                "acceptance": acceptance_result,
            }

        ok, result = self._execute_agent(
            handle,
            node,
            profile,
            handle.permission_snapshot,
            worktree=self.workspace_root,
        )
        return ("success", result) if ok else ("failure", result)

    def _finalize_terminal_run(
        self, handle: RuntimeHandle, git_manager: GitWorktreeManager
    ) -> None:
        run = handle.run
        if run.status == "succeeded":
            writeback = self._writeback_tasks(handle=handle, git_manager=git_manager)
            error = writeback.get("error") if isinstance(writeback, dict) else None
            if isinstance(error, dict):
                run.status = "failed"
                self._emit(
                    handle,
                    stage="summary",
                    event="TASK_WRITEBACK_FAILED",
                    level="error",
                    message="task write-back failed",
                    meta=error,
                )
            else:
                run.metadata["task_writeback"] = writeback
        run.completed_at = datetime.now(timezone.utc).isoformat()
        self._write_artifacts(run)
        self._emit(
            handle,
            stage="summary",
            event="RUN_DONE",
            level="info"
            if run.status == "succeeded"
            else "error"
            if run.status == "failed"
            else "warn",
            message=f"run {run.status}",
        )
        self._persist_runtime_state(handle, run.status)
        self.run_store.release_lock(run.id)
        handle.finished_event.set()

    def _execute_run(self, handle: RuntimeHandle) -> None:
        run = handle.run
        runtime = handle.runtime
        git_manager = GitWorktreeManager(self.workspace_root, run.id)
        run.metadata["git_state"] = git_manager.bootstrap_state(
            run.metadata.get("git_state")
        )

        max_steps = int(handle.plan.constraints.max_total_steps)
        max_runtime = int(handle.plan.constraints.max_runtime_seconds)
        fail_fast = bool(handle.plan.constraints.fail_fast)

        steps = 0
        run_started = time.perf_counter()

        if run.status != "running":
            run.status = "running"
            if any(evt.get("event") == "RUN_STARTED" for evt in run.events):
                self._emit(
                    handle,
                    stage="plan",
                    event="RUN_RECOVERED",
                    level="info",
                    message="run recovered",
                )
            else:
                self._emit(
                    handle,
                    stage="plan",
                    event="RUN_STARTED",
                    level="info",
                    message="run started",
                )
        self._persist_runtime_state(handle, "running")

        try:
            while True:
                if handle.cancel_event.is_set():
                    run.status = "cancelled"
                    for node in run.nodes.values():
                        if node.status in {"queued", "running"}:
                            node.status = "blocked"
                    break

                if time.perf_counter() - run_started > max_runtime:
                    run.status = "failed"
                    self._emit(
                        handle,
                        stage="summary",
                        event="RUN_TIMEOUT",
                        level="error",
                        message="run exceeded max runtime",
                    )
                    break

                if (
                    handle.pause_event.is_set()
                    and run.status != "paused_recovery_required"
                ):
                    run.status = "paused"
                    self._persist_runtime_state(handle, "paused")
                    time.sleep(0.1)
                    continue

                ready_nodes = [
                    node
                    for node in runtime.nodes
                    if run.nodes[node.id].status == "queued"
                    and all(
                        run.nodes.get(dep) and run.nodes[dep].status == "succeeded"
                        for dep in node.depends_on
                    )
                ]

                if not ready_nodes:
                    queued = any(node.status == "queued" for node in run.nodes.values())
                    running = any(
                        node.status == "running" for node in run.nodes.values()
                    )
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
                            node.id: pool.submit(
                                self._run_node, handle, node, git_manager
                            )
                            for node in batch
                        }
                        for node in batch:
                            try:
                                results[node.id] = futures[node.id].result()
                            except Exception as exc:  # noqa: BLE001
                                results[node.id] = (
                                    "failure",
                                    {"reason": "runtime_error", "error": str(exc)},
                                )
                else:
                    current = batch[0]
                    try:
                        results[current.id] = self._run_node(
                            handle, current, git_manager
                        )
                    except Exception as exc:  # noqa: BLE001
                        results[current.id] = (
                            "failure",
                            {"reason": "runtime_error", "error": str(exc)},
                        )

                for current in batch:
                    outcome, result = results[current.id]
                    rec = run.nodes[current.id]

                    if outcome == "recovery_required":
                        outcome, result = self._attempt_inline_auto_recovery(
                            handle, current, dict(result), git_manager
                        )
                    if outcome == "recovery_required":
                        rec.status = "queued"
                        rec.result = result
                        self._handle_recovery_required(handle, current, result)
                        break

                    if outcome == "success":
                        self._apply_agent_result(
                            handle,
                            current,
                            success=True,
                            result=result,
                            fail_fast=fail_fast,
                        )
                    else:
                        self._apply_agent_result(
                            handle,
                            current,
                            success=False,
                            result=result,
                            fail_fast=fail_fast,
                        )

                run.active_node_id = None

                if run.status == "paused_recovery_required":
                    self._checkpoint(handle, status="paused_recovery_required")
                    self._persist_runtime_state(handle, "paused_recovery_required")
                    break

                self._cleanup_completed_phases(handle, git_manager)

                self._checkpoint(handle, status="running")

                if steps >= max_steps:
                    run.status = "failed"
                    self._emit(
                        handle,
                        stage="summary",
                        event="RUN_LIMIT_REACHED",
                        level="error",
                        message="run exceeded max steps",
                    )
                    break

            if run.status == "running":
                failed = any(node.status == "failed" for node in run.nodes.values())
                blocked = any(node.status == "blocked" for node in run.nodes.values())
                if failed or blocked:
                    run.status = "failed"
                else:
                    run.status = "succeeded"

            if run.status == "paused_recovery_required":
                total_seconds = max(0.0, time.perf_counter() - run_started)
                run.metadata["run_metrics"] = self._build_run_metrics(
                    run,
                    execution_seconds=total_seconds,
                    cleanup_seconds=0.0,
                    total_seconds=total_seconds,
                ).model_dump(mode="json")
                self._write_artifacts(run)
                self._persist_runtime_state(handle, "paused_recovery_required")
                self.run_store.release_lock(run.id)
                handle.finished_event.set()
                return

            cleanup_started = time.perf_counter()
            cleanup_notes = git_manager.cleanup_all(
                run.metadata.setdefault("git_state", {})
            )
            if cleanup_notes:
                self._emit(
                    handle,
                    stage="orchestrator",
                    event="CLEANUP_DONE",
                    level="info",
                    message="workspace cleanup completed",
                    meta={"items": cleanup_notes},
                )
            run.metadata["stale_artifacts"] = git_manager.detect_stale_artifacts(
                active_run_ids=self.list_active_run_ids(),
                max_age_hours=24,
            )
            cleanup_seconds = max(0.0, time.perf_counter() - cleanup_started)
            total_seconds = max(0.0, time.perf_counter() - run_started)
            execution_seconds = max(0.0, total_seconds - cleanup_seconds)
            run.metadata["run_metrics"] = self._build_run_metrics(
                run,
                execution_seconds=execution_seconds,
                cleanup_seconds=cleanup_seconds,
                total_seconds=total_seconds,
            ).model_dump(mode="json")
            self._finalize_terminal_run(handle, git_manager)
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
            total_seconds = max(0.0, time.perf_counter() - run_started)
            run.metadata["run_metrics"] = self._build_run_metrics(
                run,
                execution_seconds=total_seconds,
                cleanup_seconds=0.0,
                total_seconds=total_seconds,
            ).model_dump(mode="json")
            self._finalize_terminal_run(handle, git_manager)
