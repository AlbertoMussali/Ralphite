from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ralphite.engine.config import LocalConfig
from ralphite.engine.git_worktree import GitRequiredError, GitWorktreeManager
from ralphite.engine.models import NodeRuntimeState, RunPersistenceState, RunViewState
from ralphite.engine.recovery import to_paused_for_recovery
from ralphite.engine.templates import dump_yaml, make_goal_plan, seed_starter_if_missing
from ralphite.engine.templates import versioned_filename
from ralphite.engine.validation import (
    parse_plan_with_defaults,
    parse_plan_yaml,
    validate_plan_content,
)
from ralphite.schemas.validation import compile_plan

if TYPE_CHECKING:
    from ralphite.engine.orchestrator import RuntimeHandle
    from ralphite.engine.run_store import RunStore
    from ralphite.engine.state_manager import RunStateManager
    from ralphite.engine.store import HistoryStore
    from ralphite.engine.structure_compiler import RuntimeExecutionPlan


class RuntimeBootstrap:
    def __init__(
        self,
        *,
        workspace_root: Path,
        paths: dict[str, Path],
        config: LocalConfig,
        run_store: "RunStore",
        history: "HistoryStore",
        state_manager: "RunStateManager",
        active: dict[str, "RuntimeHandle"],
        materialize_runtime_plan: Any,
    ) -> None:
        self.workspace_root = workspace_root
        self.paths = paths
        self.config = config
        self.run_store = run_store
        self.history = history
        self.state_manager = state_manager
        self.active = active
        self.materialize_runtime_plan = materialize_runtime_plan

    def initialize_workspace(self, *, bootstrap: bool) -> None:
        if bootstrap:
            seed_starter_if_missing(self.paths["plans"])
        self.bootstrap_recovery_candidates()

    def bootstrap_recovery_candidates(self) -> None:
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
                path
                for path in self.paths["plans"].iterdir()
                if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def default_permission_snapshot(self) -> dict[str, list[str]]:
        return {
            "allow_tools": list(self.config.allow_tools),
            "deny_tools": list(self.config.deny_tools),
            "allow_mcps": list(self.config.allow_mcps),
            "deny_mcps": list(self.config.deny_mcps),
        }

    def resolve_plan_path(self, plan_ref: str | None) -> Path:
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
                default_path = self.resolve_plan_path(self.config.default_plan)
                if default_path.exists():
                    return default_path
            except FileNotFoundError:
                pass

        plans = self.list_plans()
        if not plans:
            raise FileNotFoundError("no plans found in .ralphite/plans")

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
        filename = versioned_filename(plan["plan_id"], filename_hint)
        path = self.paths["plans"] / filename
        path.write_text(dump_yaml(plan), encoding="utf-8")
        return path

    def task_surface_map(self, tasks: list[Any]) -> dict[str, list[str]]:
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

    def task_write_policy_map(self, tasks: list[Any]) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        for task in tasks:
            raw = (
                getattr(task, "write_policy", {})
                if isinstance(getattr(task, "write_policy", {}), dict)
                else {}
            )
            allowed = [
                str(item).strip().strip("/\\")
                for item in raw.get("allowed_write_roots", [])
                if str(item).strip()
            ]
            forbidden = [
                str(item).strip().strip("/\\")
                for item in raw.get("forbidden_write_roots", [])
                if str(item).strip()
            ]
            if not allowed:
                for artifact in (
                    getattr(task, "acceptance_required_artifacts", []) or []
                ):
                    path_glob = (
                        str(artifact.get("path_glob") or "").strip().replace("\\", "/")
                    )
                    if not path_glob:
                        continue
                    if "/" in path_glob:
                        allowed.append(path_glob.split("/", 1)[0])
                    elif not any(ch in path_glob for ch in "*?["):
                        allowed.append(path_glob)
            mapping[str(getattr(task, "id", ""))] = {
                "allowed_write_roots": sorted(
                    dict.fromkeys(item for item in allowed if item)
                ),
                "forbidden_write_roots": sorted(
                    dict.fromkeys(item for item in forbidden if item)
                ),
                "allow_plan_edits": bool(raw.get("allow_plan_edits")),
                "allow_root_writes": bool(raw.get("allow_root_writes")),
            }
        return mapping

    def runtime_metadata(
        self, runtime: "RuntimeExecutionPlan", *, tasks: list[Any]
    ) -> dict[str, Any]:
        lane_map: dict[str, str] = {}
        phase_map: dict[str, str] = {}
        role_map: dict[str, str] = {}
        phase_nodes: dict[str, list[str]] = defaultdict(list)
        cell_map: dict[str, str] = {}
        team_map: dict[str, str] = {}
        behavior_map: dict[str, str] = {}
        task_surface_map = self.task_surface_map(tasks)
        task_write_policy_map = self.task_write_policy_map(tasks)
        node_surface_map: dict[str, list[str]] = {}
        node_write_policy_map: dict[str, dict[str, Any]] = {}

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
                node_write_policy_map[node.id] = dict(
                    task_write_policy_map.get(node.source_task_id, {})
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
            "task_write_policy_map": task_write_policy_map,
            "node_write_policy_map": node_write_policy_map,
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

    def run_start_preflight(
        self,
        *,
        list_recoverable_runs: Any,
        stale_artifact_report: Any,
    ) -> dict[str, Any]:
        recoverable_runs = list_recoverable_runs()
        stale = stale_artifact_report(max_age_hours=0)
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
            "reason": ("stale_recovery_state_present" if blocking_reasons else "ok"),
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
            path = self.resolve_plan_path(plan_ref)
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

    def prepare_run(
        self,
        *,
        handle_cls: type["RuntimeHandle"],
        plan_ref: str | None,
        plan_content: str | None,
        backend_override: str | None,
        model_override: str | None,
        reasoning_effort_override: str | None,
        permission_snapshot: dict[str, list[str]] | None,
        metadata: dict[str, Any] | None,
        first_failure_recovery: str | None,
    ) -> "RuntimeHandle":
        compile_started = time.perf_counter()
        if plan_content is None:
            source_path = self.resolve_plan_path(plan_ref)
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
        runtime, runtime_meta = self.materialize_runtime_plan(plan_document)
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

        handle = handle_cls(
            run=run,
            plan=plan_document,
            runtime=runtime,
            profile_map=profile_map,
            permission_snapshot=snapshot,
        )
        self.active[run_id] = handle
        self.state_manager.persist_runtime_state(handle, "queued")
        return handle

    def rebuild_handle_for_recovery(
        self, *, handle_cls: type["RuntimeHandle"], run_id: str
    ) -> "RuntimeHandle | None":
        state = self.run_store.load_state(run_id)
        if not state:
            return None

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
        runtime, runtime_meta = self.materialize_runtime_plan(plan_document)
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
        run.metadata.setdefault(
            "task_write_policy_map", runtime_meta.get("task_write_policy_map", {})
        )
        run.metadata.setdefault(
            "node_write_policy_map", runtime_meta.get("node_write_policy_map", {})
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
        git_manager = GitWorktreeManager(self.workspace_root, run_id)
        git_reconciliation = git_manager.reconcile_state(
            run.metadata.setdefault("git_state", {})
        )
        run.metadata["retained_work"] = list(
            run.metadata.setdefault("git_state", {}).get("retained_work", [])
        )
        run.metadata["git_reconciliation"] = git_reconciliation
        run.metadata.setdefault("first_failure_recovery", "none")

        snapshot = run.metadata.get("permission_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = self.default_permission_snapshot()

        profile_map = {profile.id: profile for profile in plan_document.agents}
        handle = handle_cls(
            run=run,
            plan=plan_document,
            runtime=runtime,
            profile_map=profile_map,
            permission_snapshot=snapshot,
            seq=paused_state.last_seq,
        )
        handle.pause_event.set()
        self.active[run_id] = handle
        return handle

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
