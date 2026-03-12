from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from ralphite.engine.git_worktree import GitWorktreeManager
from ralphite.engine.structure_compiler import RuntimeNodeSpec
from ralphite.engine.taxonomy import classify_failure

if TYPE_CHECKING:
    from ralphite.engine.runtime_artifacts import RuntimeArtifacts
    from ralphite.engine.runtime_events import RuntimeEvents
    from ralphite.engine.runtime_node_runner import RuntimeNodeRunner
    from ralphite.engine.runtime_recovery_manager import RuntimeRecoveryManager
    from ralphite.engine.orchestrator import RuntimeHandle


class RuntimeExecutionEngine:
    def __init__(
        self,
        *,
        workspace_root: Path,
        node_runner: "RuntimeNodeRunner",
        recovery_manager: "RuntimeRecoveryManager",
        artifacts: "RuntimeArtifacts",
        events: "RuntimeEvents",
        persist_runtime_state: Any,
        checkpoint: Any,
        run_node_callback: Any | None = None,
        execute_agent_callback: Any | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.node_runner = node_runner
        self.recovery_manager = recovery_manager
        self.artifacts = artifacts
        self.events = events
        self.persist_runtime_state = persist_runtime_state
        self.checkpoint = checkpoint
        self.run_node_callback = run_node_callback
        self.execute_agent_callback = execute_agent_callback

    def build_auto_recovery_prompt(
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

    def attempt_inline_auto_recovery(
        self,
        handle: "RuntimeHandle",
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
        prompt = self.build_auto_recovery_prompt(
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
            self.events.emit(
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

        self.events.emit(
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
        execute_agent = (
            self.execute_agent_callback or self.node_runner.execute_agent_impl
        )
        agent_ok, agent_result = execute_agent(
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
            self.events.emit(
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
            ignore_overlap_paths=self.node_runner.integration_overlap_ignore_paths(
                handle
            ),
        )
        if status == "success":
            recovery["auto_attempt_status"] = "succeeded"
            details["auto_recovery"] = {
                "mode": "agent_best_effort",
                "status": "succeeded",
            }
            self.events.emit(
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
        self.events.emit(
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

    def start_node_execution(
        self, handle: "RuntimeHandle", node: RuntimeNodeSpec
    ) -> None:
        rec = handle.run.nodes[node.id]
        rec.status = "running"
        rec.attempt_count += 1
        handle.run.active_node_id = node.id
        self.events.emit(
            handle,
            stage="task",
            event="NODE_STARTED",
            level="info",
            message="node started",
            group=node.group,
            task_id=node.id,
            meta={"attempt": rec.attempt_count},
        )
        self.events.emit_node_started(handle, node)

    def handle_recovery_required(
        self,
        handle: "RuntimeHandle",
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
        self.events.record_interruption_reason(
            handle, str(details.get("reason") or "runtime_error")
        )

        self.events.emit(
            handle,
            stage="orchestrator",
            event="RECOVERY_REQUIRED",
            level="error",
            message="merge/integration conflict requires recovery",
            group=node.phase,
            task_id=node.id,
            meta=details,
        )
        self.retain_all_managed_work(
            handle,
            GitWorktreeManager(self.workspace_root, handle.run.id),
            reason=str(details.get("reason") or "recovery_required"),
            failure_title="Recovery Required",
        )

        handle.run.status = "paused_recovery_required"
        handle.run.active_node_id = None
        handle.pause_event.set()

    def sync_retained_work_metadata(
        self, handle: "RuntimeHandle", git_manager: GitWorktreeManager
    ) -> None:
        git_state = (
            handle.run.metadata.get("git_state", {})
            if isinstance(handle.run.metadata.get("git_state"), dict)
            else {}
        )
        if not isinstance(git_state, dict):
            return
        reconciliation = git_manager.reconcile_state(git_state)
        handle.run.metadata["retained_work"] = list(git_state.get("retained_work", []))
        handle.run.metadata["git_reconciliation"] = reconciliation

    def retain_result_targets(
        self,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        result: dict[str, Any],
        git_manager: GitWorktreeManager,
    ) -> list[dict[str, Any]]:
        git_state = handle.run.metadata.setdefault("git_state", {})
        targets = result.get("preserve_targets") if isinstance(result, dict) else []
        if not isinstance(targets, list):
            targets = []
        retained: list[dict[str, Any]] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            retained.append(
                git_manager.retain_target(
                    git_state,
                    scope=str(target.get("scope") or "worker"),
                    reason=str(result.get("reason") or "runtime_error"),
                    phase=str(target.get("phase") or node.phase or ""),
                    node_id=str(target.get("node_id") or node.id),
                    worktree_path=str(target.get("worktree_path") or "") or None,
                    branch=str(target.get("branch") or "") or None,
                    failure_title=str(result.get("failure_title") or ""),
                    failed_command=str(result.get("failed_command") or ""),
                    error=str(result.get("error") or ""),
                    stdout=str(result.get("stdout") or ""),
                    stderr=str(result.get("stderr") or ""),
                    backend_payload=(
                        result.get("backend_payload")
                        if isinstance(result.get("backend_payload"), dict)
                        else {}
                    ),
                    diagnostics=(
                        result.get("diagnostics")
                        if isinstance(result.get("diagnostics"), dict)
                        else {}
                    ),
                    committed=(
                        bool(target.get("committed"))
                        if target.get("committed") is not None
                        else None
                    ),
                )
            )
        if retained:
            self.sync_retained_work_metadata(handle, git_manager)
        return retained

    def retain_all_managed_work(
        self,
        handle: "RuntimeHandle",
        git_manager: GitWorktreeManager,
        *,
        reason: str,
        failure_title: str = "",
    ) -> list[dict[str, Any]]:
        retained = git_manager.retain_all_managed_work(
            handle.run.metadata.setdefault("git_state", {}),
            reason=reason,
            failure_title=failure_title,
        )
        if retained:
            self.sync_retained_work_metadata(handle, git_manager)
        return retained

    def apply_agent_result(
        self,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        *,
        success: bool,
        result: dict[str, Any],
        fail_fast: bool,
        git_manager: GitWorktreeManager,
    ) -> None:
        rec = handle.run.nodes[node.id]
        if success:
            rec.status = "succeeded"
            rec.result = result
            self.events.emit(
                handle,
                stage="task",
                event="NODE_RESULT",
                level="info",
                message="node completed",
                group=node.group,
                task_id=node.id,
                meta={"status": rec.status, "result": result},
            )
            self.events.emit_node_completed(handle, node, True)
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
            "backend_payload_missing",
            "backend_payload_malformed",
            "backend_out_of_worktree_claim",
            "backend_out_of_worktree_mutation",
            "backend_worktree_missing",
            "defaults.placeholder_invalid",
        }
        if reason not in non_retryable and rec.attempt_count <= max_retries:
            rec.status = "queued"
            rec.result = result
            handle.run.retry_count += 1
            self.events.emit(
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
        retained = self.retain_result_targets(
            handle, node, enriched_result, git_manager
        )
        if retained:
            enriched_result["retained_work"] = retained
        rec.result = enriched_result
        self.events.emit(
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
        self.events.emit_node_completed(handle, node, False)

        if fail_fast:
            for queued in handle.run.nodes.values():
                if queued.status == "queued":
                    queued.status = "blocked"

    def high_overlap_surfaces(
        self, handle: "RuntimeHandle", nodes: list[RuntimeNodeSpec]
    ) -> list[str]:
        if len(nodes) < 2:
            return []
        token_counts: dict[str, int] = {}
        for node in nodes:
            for token in self.node_runner.node_surfaces(handle, node):
                token_counts[token] = int(token_counts.get(token, 0)) + 1
        return sorted(token for token, count in token_counts.items() if count > 1)

    def cleanup_completed_phases(
        self, handle: "RuntimeHandle", git_manager: GitWorktreeManager
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
            if not git_manager.phase_cleanup_allowed(
                handle.run.metadata.setdefault("git_state", {}), str(phase)
            ):
                continue
            cleanup_notes = git_manager.cleanup_phase(
                handle.run.metadata.setdefault("git_state", {}),
                str(phase),
            )
            if cleanup_notes:
                self.events.emit(
                    handle,
                    stage="orchestrator",
                    event="PHASE_CLEANUP_DONE",
                    level="info",
                    message="phase git artifacts cleaned after successful phase completion",
                    group=str(phase),
                    meta={"items": cleanup_notes},
                )
            cleaned.append(str(phase))

    def choose_batch(
        self, handle: "RuntimeHandle", ready_nodes: list[RuntimeNodeSpec]
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

        if worker_same_block and len({node.lane for node in worker_same_block}) > 1:
            overlap_tokens = self.high_overlap_surfaces(handle, worker_same_block)
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
                    self.events.emit(
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

    def execute_run(self, handle: "RuntimeHandle") -> None:
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
                self.events.emit(
                    handle,
                    stage="plan",
                    event="RUN_RECOVERED",
                    level="info",
                    message="run recovered",
                )
            else:
                self.events.emit(
                    handle,
                    stage="plan",
                    event="RUN_STARTED",
                    level="info",
                    message="run started",
                )
        self.persist_runtime_state(handle, "running")

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
                    self.events.emit(
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
                    self.persist_runtime_state(handle, "paused")
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

                batch = self.choose_batch(handle, ready_nodes)
                for current in batch:
                    self.start_node_execution(handle, current)
                steps += len(batch)

                results: dict[str, tuple[str, dict[str, Any]]] = {}
                if len(batch) > 1 and all(node.role == "worker" for node in batch):
                    with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                        futures = {
                            node.id: pool.submit(
                                (
                                    self.run_node_callback
                                    or self.node_runner.run_node_impl
                                ),
                                handle,
                                node,
                                git_manager,
                            )
                            for node in batch
                        }
                        for node in batch:
                            try:
                                results[node.id] = futures[node.id].result()
                            except Exception as exc:
                                results[node.id] = (
                                    "failure",
                                    {"reason": "runtime_error", "error": str(exc)},
                                )
                else:
                    current = batch[0]
                    try:
                        results[current.id] = (
                            self.run_node_callback or self.node_runner.run_node_impl
                        )(handle, current, git_manager)
                    except Exception as exc:
                        results[current.id] = (
                            "failure",
                            {"reason": "runtime_error", "error": str(exc)},
                        )

                for current in batch:
                    outcome, result = results[current.id]
                    rec = run.nodes[current.id]
                    if outcome == "recovery_required":
                        outcome, result = self.attempt_inline_auto_recovery(
                            handle, current, dict(result), git_manager
                        )
                    if outcome == "recovery_required":
                        rec.status = "queued"
                        rec.result = result
                        self.handle_recovery_required(handle, current, result)
                        break

                    self.apply_agent_result(
                        handle,
                        current,
                        success=outcome == "success",
                        result=result,
                        fail_fast=fail_fast,
                        git_manager=git_manager,
                    )

                run.active_node_id = None

                if run.status == "paused_recovery_required":
                    self.checkpoint(handle, status="paused_recovery_required")
                    self.persist_runtime_state(handle, "paused_recovery_required")
                    break

                self.cleanup_completed_phases(handle, git_manager)
                self.checkpoint(handle, status="running")

                if steps >= max_steps:
                    run.status = "failed"
                    self.events.emit(
                        handle,
                        stage="summary",
                        event="RUN_LIMIT_REACHED",
                        level="error",
                        message="run exceeded max steps",
                    )
                    break

            if run.status == "running":
                derived_status = self.recovery_manager.recompute_run_status(handle)
                has_incomplete_nodes = any(
                    node.status in {"queued", "blocked"} for node in run.nodes.values()
                )
                if derived_status == "paused":
                    run.status = "failed"
                    self.events.emit(
                        handle,
                        stage="summary",
                        event="RUN_INCOMPLETE_BLOCKED",
                        level="error",
                        message="run terminated with incomplete blocked or queued nodes",
                    )
                else:
                    run.status = derived_status
                    if has_incomplete_nodes:
                        self.events.emit(
                            handle,
                            stage="summary",
                            event="RUN_INCOMPLETE_BLOCKED",
                            level="error",
                            message="run terminated with incomplete blocked or queued nodes",
                        )

            if run.status == "paused_recovery_required":
                self.retain_all_managed_work(
                    handle,
                    git_manager,
                    reason="paused_recovery_required",
                    failure_title="Recovery Required",
                )
                total_seconds = max(0.0, time.perf_counter() - run_started)
                run.metadata["run_metrics"] = self.artifacts.build_run_metrics(
                    run,
                    execution_seconds=total_seconds,
                    cleanup_seconds=0.0,
                    total_seconds=total_seconds,
                ).model_dump(mode="json")
                self.artifacts.write_artifacts(run)
                self.persist_runtime_state(handle, "paused_recovery_required")
                self.artifacts.run_store.release_lock(run.id)
                handle.finished_event.set()
                return

            total_seconds = max(0.0, time.perf_counter() - run_started)
            run.metadata["run_metrics"] = self.artifacts.build_run_metrics(
                run,
                execution_seconds=total_seconds,
                cleanup_seconds=0.0,
                total_seconds=total_seconds,
            ).model_dump(mode="json")
            cleanup_policy = {
                "status": run.status,
                "cleanup_allowed": run.status == "succeeded",
                "mode": "terminal",
            }
            if run.status != "succeeded":
                advice = classify_failure(
                    next(
                        (
                            str(node.result.get("reason") or "runtime_error")
                            for node in run.nodes.values()
                            if node.status == "failed" and isinstance(node.result, dict)
                        ),
                        "runtime_error",
                    )
                )
                retained = self.retain_all_managed_work(
                    handle,
                    git_manager,
                    reason=f"terminal_{run.status}",
                    failure_title=advice.title,
                )
                cleanup_policy["mode"] = "preserved"
                cleanup_policy["retained_items"] = len(retained)
                if retained:
                    self.events.emit(
                        handle,
                        stage="orchestrator",
                        event="CLEANUP_SKIPPED",
                        level="warn",
                        message="managed git artifacts preserved after non-success run",
                        meta={
                            "status": run.status,
                            "retained_items": len(retained),
                        },
                    )
                run.metadata["cleanup_decision"] = cleanup_policy
                run.metadata["stale_artifacts"] = git_manager.detect_stale_artifacts(
                    active_run_ids=self.recovery_manager.list_active_run_ids(),
                    max_age_hours=24,
                )
                self.artifacts.finalize_terminal_run(handle, git_manager)
                return

            run.metadata["cleanup_decision"] = cleanup_policy
            self.artifacts.prepare_terminal_artifacts(handle, git_manager)

            cleanup_started = time.perf_counter()
            cleanup_notes = git_manager.cleanup_all(
                run.metadata.setdefault("git_state", {})
            )
            cleanup_seconds = max(0.0, time.perf_counter() - cleanup_started)
            if cleanup_notes:
                self.events.emit(
                    handle,
                    stage="orchestrator",
                    event="CLEANUP_DONE",
                    level="info",
                    message="workspace cleanup completed",
                    meta={"items": cleanup_notes},
                )
            run.metadata["stale_artifacts"] = git_manager.detect_stale_artifacts(
                active_run_ids=self.recovery_manager.list_active_run_ids(),
                max_age_hours=24,
            )
            total_seconds = max(0.0, time.perf_counter() - run_started)
            execution_seconds = max(0.0, total_seconds - cleanup_seconds)
            run.metadata["run_metrics"] = self.artifacts.build_run_metrics(
                run,
                execution_seconds=execution_seconds,
                cleanup_seconds=cleanup_seconds,
                total_seconds=total_seconds,
            ).model_dump(mode="json")
            self.artifacts.write_artifacts(run)
            self.artifacts.finish_terminal_run(handle)
        except Exception as exc:
            run.status = "failed"
            self.events.emit(
                handle,
                stage="summary",
                event="RUN_INTERNAL_ERROR",
                level="error",
                message=f"run crashed: {exc}",
                meta={"error": str(exc)},
            )
            total_seconds = max(0.0, time.perf_counter() - run_started)
            run.metadata["run_metrics"] = self.artifacts.build_run_metrics(
                run,
                execution_seconds=total_seconds,
                cleanup_seconds=0.0,
                total_seconds=total_seconds,
            ).model_dump(mode="json")
            self.retain_all_managed_work(
                handle,
                git_manager,
                reason="internal_error",
                failure_title="Runtime Error",
            )
            self.artifacts.finalize_terminal_run(handle, git_manager)
