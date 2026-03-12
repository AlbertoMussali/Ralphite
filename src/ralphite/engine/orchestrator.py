from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
import threading
from typing import Any, Generator

from ralphite.engine.config import LocalConfig, ensure_workspace_layout, load_config
from ralphite.engine.event_logger import RunEventLogger
from ralphite.engine.git_orchestrator import GitOrchestrator
from ralphite.engine.headless_agent import build_worker_subprocess_env
from ralphite.engine.models import RunViewState
from ralphite.engine.run_store import RunStore
from ralphite.engine.runtime_artifacts import RuntimeArtifacts
from ralphite.engine.runtime_bootstrap import RuntimeBootstrap
from ralphite.engine.runtime_events import RuntimeEvents
from ralphite.engine.runtime_execution_engine import RuntimeExecutionEngine
from ralphite.engine.runtime_node_runner import RuntimeNodeRunner
from ralphite.engine.runtime_recovery_manager import RuntimeRecoveryManager
from ralphite.engine.state_manager import RunStateManager
from ralphite.engine.store import HistoryStore
from ralphite.engine.structure_compiler import (
    RuntimeExecutionPlan,
    RuntimeNodeSpec,
    compile_execution_structure,
)
from ralphite.engine.task_parser import parse_plan_tasks
from ralphite.engine.templates import versioned_filename
from ralphite.schemas.plan import AgentSpec, PlanSpec


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

        self.state_manager = RunStateManager(self.run_store, self.history)
        self.event_logger = RunEventLogger(self.run_store, self.history, self.active)
        self.git_orchestrator = GitOrchestrator(self.workspace_root)

        self.events = RuntimeEvents(self.event_logger)
        self.node_runner = RuntimeNodeRunner(
            workspace_root=self.workspace_root,
            config=self.config,
            git_orchestrator=self.git_orchestrator,
            execute_agent_callback=lambda handle, node, profile, snapshot, *, worktree: (
                self._execute_agent(handle, node, profile, snapshot, worktree=worktree)
            ),
            evaluate_acceptance_callback=lambda node, commit_meta, *, timeout_seconds: (
                self._evaluate_acceptance(
                    node, commit_meta, timeout_seconds=timeout_seconds
                )
            ),
            build_worker_env=lambda *, worktree: build_worker_subprocess_env(
                worktree=worktree
            ),
        )
        self.bootstrap_service = RuntimeBootstrap(
            workspace_root=self.workspace_root,
            paths=self.paths,
            config=self.config,
            run_store=self.run_store,
            history=self.history,
            state_manager=self.state_manager,
            active=self.active,
            materialize_runtime_plan=self._materialize_runtime_plan,
        )
        self.artifacts = RuntimeArtifacts(
            paths=self.paths,
            run_store=self.run_store,
            state_manager=self.state_manager,
            events=self.events,
            writeback_target=self._writeback_target,
        )
        self.recovery_manager = RuntimeRecoveryManager(
            workspace_root=self.workspace_root,
            run_store=self.run_store,
            history=self.history,
            state_manager=self.state_manager,
            active=self.active,
            bootstrap=self.bootstrap_service,
            node_runner=self.node_runner,
            artifacts=self.artifacts,
            events=self.events,
            handle_cls=RuntimeHandle,
            evaluate_acceptance_callback=lambda node, commit_meta, *, timeout_seconds: (
                self._evaluate_acceptance(
                    node, commit_meta, timeout_seconds=timeout_seconds
                )
            ),
        )
        self.execution_engine = RuntimeExecutionEngine(
            workspace_root=self.workspace_root,
            node_runner=self.node_runner,
            recovery_manager=self.recovery_manager,
            artifacts=self.artifacts,
            events=self.events,
            persist_runtime_state=self._persist_runtime_state,
            checkpoint=self._checkpoint,
            run_node_callback=lambda handle, node, git_manager: self._run_node(
                handle, node, git_manager
            ),
            execute_agent_callback=lambda handle, node, profile, snapshot, *, worktree: (
                self._execute_agent(handle, node, profile, snapshot, worktree=worktree)
            ),
        )

        self.bootstrap_service.initialize_workspace(bootstrap=bootstrap)

    def list_plans(self) -> list[Path]:
        return self.bootstrap_service.list_plans()

    def default_permission_snapshot(self) -> dict[str, list[str]]:
        return self.bootstrap_service.default_permission_snapshot()

    def list_active_run_ids(self) -> list[str]:
        return self.recovery_manager.list_active_run_ids()

    def _resolve_plan_path(self, plan_ref: str | None) -> Path:
        return self.bootstrap_service.resolve_plan_path(plan_ref)

    def goal_to_plan(self, goal: str, filename_hint: str = "goal") -> Path:
        return self.bootstrap_service.goal_to_plan(goal, filename_hint)

    def _task_surface_map(self, tasks: list[Any]) -> dict[str, list[str]]:
        return self.bootstrap_service.task_surface_map(tasks)

    def _task_write_policy_map(self, tasks: list[Any]) -> dict[str, dict[str, Any]]:
        return self.bootstrap_service.task_write_policy_map(tasks)

    def _runtime_metadata(
        self, runtime: RuntimeExecutionPlan, *, tasks: list[Any]
    ) -> dict[str, Any]:
        return self.bootstrap_service.runtime_metadata(runtime, tasks=tasks)

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
            raise ValueError(f"validation_error: {details}")
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
        return self.bootstrap_service.git_runtime_status()

    def git_repository_status(self) -> dict[str, Any]:
        return self.bootstrap_service.git_repository_status()

    def require_git_workspace(self) -> None:
        self.bootstrap_service.require_git_workspace()

    def require_git_repository(self) -> None:
        self.bootstrap_service.require_git_repository()

    def run_start_preflight(self) -> dict[str, Any]:
        return self.bootstrap_service.run_start_preflight(
            list_recoverable_runs=self.list_recoverable_runs,
            stale_artifact_report=self.stale_artifact_report,
        )

    def collect_requirements(
        self, plan_ref: str | None = None, plan_content: str | None = None
    ) -> dict[str, list[str]]:
        return self.bootstrap_service.collect_requirements(plan_ref, plan_content)

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

        handle = self.bootstrap_service.prepare_run(
            handle_cls=RuntimeHandle,
            plan_ref=plan_ref,
            plan_content=plan_content,
            backend_override=backend_override,
            model_override=model_override,
            reasoning_effort_override=reasoning_effort_override,
            permission_snapshot=permission_snapshot,
            metadata=metadata,
            first_failure_recovery=first_failure_recovery,
        )
        handle.thread = threading.Thread(
            target=self.execution_engine.execute_run, args=(handle,), daemon=False
        )
        handle.thread.start()
        return handle.run.id

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
        return self.recovery_manager.list_recoverable_runs()

    def stale_artifact_report(
        self, max_age_hours: int = 24
    ) -> dict[str, list[dict[str, Any]]]:
        return self.recovery_manager.stale_artifact_report(max_age_hours=max_age_hours)

    def _retained_work_entries(self, run: RunViewState) -> list[dict[str, Any]]:
        return self.recovery_manager.retained_work_entries(run)

    def _requeue_unblocked_nodes(self, handle: RuntimeHandle) -> None:
        self.recovery_manager.requeue_unblocked_nodes(handle)

    def _recompute_run_status(self, handle: RuntimeHandle) -> str:
        return self.recovery_manager.recompute_run_status(handle)

    def _mark_phase_integrated_nodes_succeeded(
        self,
        *,
        handle: RuntimeHandle,
        phase: str,
        integration: dict[str, Any],
    ) -> None:
        self.recovery_manager.mark_phase_integrated_nodes_succeeded(
            handle=handle, phase=phase, integration=integration
        )

    def _build_node_reconciliation_rows(
        self, *, handle: RuntimeHandle, git_state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return self.recovery_manager.build_node_reconciliation_rows(
            handle=handle, git_state=git_state
        )

    def _apply_reconciled_state(
        self,
        *,
        handle: RuntimeHandle,
        checkpoint: Any,
        node_rows: list[dict[str, Any]],
        phase_rows: list[dict[str, Any]],
        git_state: dict[str, Any],
    ) -> list[str]:
        return self.recovery_manager.apply_reconciled_state(
            handle=handle,
            checkpoint=checkpoint,
            node_rows=node_rows,
            phase_rows=phase_rows,
            git_state=git_state,
        )

    def reconcile_run(self, run_id: str, *, apply: bool = False) -> dict[str, Any]:
        return self.recovery_manager.reconcile_run(run_id, apply=apply)

    def promote_salvage(self, run_id: str, node_id: str) -> tuple[bool, dict[str, Any]]:
        return self.recovery_manager.promote_salvage(run_id, node_id)

    def recover_run(self, run_id: str) -> bool:
        return self.recovery_manager.recover_run(run_id)

    def _file_has_conflict_markers(self, path: Path) -> bool:
        return self.recovery_manager.file_has_conflict_markers(path)

    def recovery_preflight(self, run_id: str) -> dict[str, Any]:
        return self.recovery_manager.recovery_preflight(run_id)

    def set_recovery_mode(
        self, run_id: str, mode: str, prompt: str | None = None
    ) -> bool:
        return self.recovery_manager.set_recovery_mode(run_id, mode, prompt=prompt)

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
                    handle, self.git_orchestrator.get_manager(run_id)
                )
                return True
            if selected not in {"manual", "agent_best_effort"}:
                return False
            preflight = self.recovery_preflight(run_id)
            if not bool(preflight.get("ok")):
                recovery["status"] = "preflight_failed"
                if preflight.get("unresolved_conflict_files"):
                    self._record_interruption_reason(
                        handle, "recovery_conflict_files_present"
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

        self.reconcile_run(run_id, apply=True)

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
            target=self.execution_engine.execute_run, args=(handle,), daemon=False
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
        self.events.emit(
            handle,
            stage=stage,
            event=event,
            level=level,
            message=message,
            group=group,
            task_id=task_id,
            meta=meta,
        )

    def _record_interruption_reason(self, handle: RuntimeHandle, reason: str) -> None:
        self.events.record_interruption_reason(handle, reason)

    def _build_auto_recovery_prompt(
        self,
        *,
        node: RuntimeNodeSpec,
        details: dict[str, Any],
        worktree: Path,
    ) -> str:
        return self.execution_engine.build_auto_recovery_prompt(
            node=node, details=details, worktree=worktree
        )

    def _attempt_inline_auto_recovery(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        details: dict[str, Any],
        git_manager: Any,
    ) -> tuple[str, dict[str, Any]]:
        return self.execution_engine.attempt_inline_auto_recovery(
            handle, node, details, git_manager
        )

    def _tool_allowed(self, tool_id: str, snapshot: dict[str, list[str]]) -> bool:
        return self.node_runner.tool_allowed(tool_id, snapshot)

    def _mcp_allowed(self, mcp_id: str, snapshot: dict[str, list[str]]) -> bool:
        return self.node_runner.mcp_allowed(mcp_id, snapshot)

    def _resolve_execution_defaults(
        self, handle: RuntimeHandle, profile: AgentSpec
    ) -> tuple[str, str, str, str]:
        return self.node_runner.resolve_execution_defaults(handle, profile)

    def _execute_agent(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        profile: AgentSpec,
        snapshot: dict[str, list[str]],
        *,
        worktree: Path,
    ) -> tuple[bool, dict[str, Any]]:
        return self.node_runner.execute_agent_impl(
            handle, node, profile, snapshot, worktree=worktree
        )

    def _emit_node_started(self, handle: RuntimeHandle, node: RuntimeNodeSpec) -> None:
        self.events.emit_node_started(handle, node)

    def _emit_node_completed(
        self, handle: RuntimeHandle, node: RuntimeNodeSpec, success: bool
    ) -> None:
        self.events.emit_node_completed(handle, node, success)

    def _start_node_execution(
        self, handle: RuntimeHandle, node: RuntimeNodeSpec
    ) -> None:
        self.execution_engine.start_node_execution(handle, node)

    def _handle_recovery_required(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        details: dict[str, Any],
    ) -> None:
        self.execution_engine.handle_recovery_required(handle, node, details)

    def _sync_retained_work_metadata(
        self, handle: RuntimeHandle, git_manager: Any
    ) -> None:
        self.execution_engine.sync_retained_work_metadata(handle, git_manager)

    def _retain_result_targets(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        result: dict[str, Any],
        git_manager: Any,
    ) -> list[dict[str, Any]]:
        return self.execution_engine.retain_result_targets(
            handle, node, result, git_manager
        )

    def _retain_all_managed_work(
        self,
        handle: RuntimeHandle,
        git_manager: Any,
        *,
        reason: str,
        failure_title: str = "",
    ) -> list[dict[str, Any]]:
        return self.execution_engine.retain_all_managed_work(
            handle, git_manager, reason=reason, failure_title=failure_title
        )

    def _apply_agent_result(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        *,
        success: bool,
        result: dict[str, Any],
        fail_fast: bool,
        git_manager: Any,
    ) -> None:
        self.execution_engine.apply_agent_result(
            handle,
            node,
            success=success,
            result=result,
            fail_fast=fail_fast,
            git_manager=git_manager,
        )

    def _build_run_metrics(
        self,
        run: RunViewState,
        *,
        execution_seconds: float,
        cleanup_seconds: float,
        total_seconds: float,
    ) -> Any:
        return self.artifacts.build_run_metrics(
            run,
            execution_seconds=execution_seconds,
            cleanup_seconds=cleanup_seconds,
            total_seconds=total_seconds,
        )

    def _write_artifacts(self, run: RunViewState) -> Any:
        return self.artifacts.write_artifacts(run)

    def _node_surfaces(self, handle: RuntimeHandle, node: RuntimeNodeSpec) -> set[str]:
        return self.node_runner.node_surfaces(handle, node)

    def _node_write_policy(
        self, handle: RuntimeHandle, node: RuntimeNodeSpec
    ) -> dict[str, Any]:
        return self.node_runner.node_write_policy(handle, node)

    def _snapshot_changed_files(self, snapshot: dict[str, Any]) -> list[str]:
        return self.node_runner.snapshot_changed_files(snapshot)

    def _classify_write_scope(
        self,
        *,
        changed_files: list[str],
        write_policy: dict[str, Any],
        plan_path: str,
    ) -> dict[str, Any]:
        return self.node_runner.classify_write_scope(
            changed_files=changed_files,
            write_policy=write_policy,
            plan_path=plan_path,
        )

    def _collect_worker_evidence(
        self,
        *,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: Any,
        worktree_path: str,
    ) -> dict[str, Any]:
        return self.node_runner.collect_worker_evidence(
            handle=handle,
            node=node,
            git_manager=git_manager,
            worktree_path=worktree_path,
        )

    def _collect_workspace_evidence(
        self,
        *,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: Any,
    ) -> dict[str, Any]:
        return self.node_runner.collect_workspace_evidence(
            handle=handle, node=node, git_manager=git_manager
        )

    def _integration_overlap_ignore_paths(self, handle: RuntimeHandle) -> list[str]:
        return self.node_runner.integration_overlap_ignore_paths(handle)

    def _workspace_bookkeeping_paths(self, handle: RuntimeHandle) -> set[str]:
        return self.node_runner.workspace_bookkeeping_paths(handle)

    def _filter_workspace_bookkeeping_files(
        self, handle: RuntimeHandle, files: list[str]
    ) -> list[str]:
        return self.node_runner.filter_workspace_bookkeeping_files(handle, files)

    def _should_attempt_backend_failure_salvage(
        self, result: dict[str, Any], evidence: dict[str, Any]
    ) -> bool:
        return self.node_runner.should_attempt_backend_failure_salvage(result, evidence)

    def _attempt_backend_failure_salvage(
        self,
        *,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: Any,
        worker_info: dict[str, Any],
        result: dict[str, Any],
        evidence: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        del worker_info
        return self.node_runner.attempt_backend_failure_salvage(
            handle=handle,
            node=node,
            git_manager=git_manager,
            result=result,
            evidence=evidence,
        )

    def _should_attempt_orchestrator_backend_failure_salvage(
        self,
        result: dict[str, Any],
        *,
        git_manager: Any,
        phase_branch: str,
        integration_worktree: str,
    ) -> bool:
        return self.node_runner.should_attempt_orchestrator_backend_failure_salvage(
            result,
            git_manager=git_manager,
            phase_branch=phase_branch,
            integration_worktree=integration_worktree,
        )

    def _attempt_orchestrator_backend_failure_salvage(
        self,
        *,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: Any,
        result: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        return self.node_runner.attempt_orchestrator_backend_failure_salvage(
            handle=handle,
            node=node,
            git_manager=git_manager,
            result=result,
        )

    def _should_attempt_workspace_backend_failure_salvage(
        self,
        result: dict[str, Any],
        *,
        evidence: dict[str, Any],
        preexisting_dirty_files: list[str],
    ) -> bool:
        return self.node_runner.should_attempt_workspace_backend_failure_salvage(
            result,
            evidence=evidence,
            preexisting_dirty_files=preexisting_dirty_files,
        )

    def _attempt_workspace_backend_failure_salvage(
        self,
        *,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: Any,
        result: dict[str, Any],
        evidence: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        return self.node_runner.attempt_workspace_backend_failure_salvage(
            handle=handle,
            node=node,
            git_manager=git_manager,
            result=result,
            evidence=evidence,
        )

    def _high_overlap_surfaces(
        self, handle: RuntimeHandle, nodes: list[RuntimeNodeSpec]
    ) -> list[str]:
        return self.execution_engine.high_overlap_surfaces(handle, nodes)

    def _cleanup_completed_phases(
        self, handle: RuntimeHandle, git_manager: Any
    ) -> None:
        self.execution_engine.cleanup_completed_phases(handle, git_manager)

    def _choose_batch(
        self, handle: RuntimeHandle, ready_nodes: list[RuntimeNodeSpec]
    ) -> list[RuntimeNodeSpec]:
        return self.execution_engine.choose_batch(handle, ready_nodes)

    def _successful_task_ids(self, handle: RuntimeHandle) -> list[str]:
        return self.artifacts.successful_task_ids(handle)

    def _writeback_tasks(
        self, *, handle: RuntimeHandle, git_manager: Any
    ) -> dict[str, Any]:
        return self.artifacts.writeback_tasks(handle=handle, git_manager=git_manager)

    def _resolve_worker_worktree(self, commit_meta: dict[str, Any]) -> Path:
        return self.node_runner.resolve_worker_worktree(commit_meta)

    def _is_worktree_relative_glob(self, path_glob: str) -> bool:
        return self.node_runner.is_worktree_relative_glob(path_glob)

    def _acceptance_command_argv(self, command: str) -> list[str]:
        return self.node_runner.acceptance_command_argv(command)

    def _expand_acceptance_command_globs(
        self, argv: list[str], *, worktree: Path
    ) -> list[str]:
        return self.node_runner.expand_acceptance_command_globs(argv, worktree=worktree)

    def _evaluate_acceptance(
        self,
        node: RuntimeNodeSpec,
        commit_meta: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> tuple[bool, dict[str, Any]]:
        return self.node_runner.evaluate_acceptance_impl(
            node, commit_meta, timeout_seconds=timeout_seconds
        )

    def _run_node(
        self,
        handle: RuntimeHandle,
        node: RuntimeNodeSpec,
        git_manager: Any,
    ) -> tuple[str, dict[str, Any]]:
        return self.node_runner.run_node_impl(handle, node, git_manager)

    def _prepare_terminal_artifacts(
        self, handle: RuntimeHandle, git_manager: Any
    ) -> None:
        self.artifacts.prepare_terminal_artifacts(handle, git_manager)

    def _finish_terminal_run(self, handle: RuntimeHandle) -> None:
        self.artifacts.finish_terminal_run(handle)

    def _finalize_terminal_run(self, handle: RuntimeHandle, git_manager: Any) -> None:
        self.artifacts.finalize_terminal_run(handle, git_manager)

    def _execute_run(self, handle: RuntimeHandle) -> None:
        self.execution_engine.execute_run(handle)
