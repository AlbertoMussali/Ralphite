from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ralphite.engine.git_worktree import GitWorktreeManager

if TYPE_CHECKING:
    from ralphite.engine.runtime_artifacts import RuntimeArtifacts
    from ralphite.engine.runtime_bootstrap import RuntimeBootstrap
    from ralphite.engine.runtime_events import RuntimeEvents
    from ralphite.engine.runtime_node_runner import RuntimeNodeRunner
    from ralphite.engine.run_store import RunStore
    from ralphite.engine.state_manager import RunStateManager
    from ralphite.engine.store import HistoryStore
    from ralphite.engine.orchestrator import RuntimeHandle


class RuntimeRecoveryManager:
    def __init__(
        self,
        *,
        workspace_root: Path,
        run_store: "RunStore",
        history: "HistoryStore",
        state_manager: "RunStateManager",
        active: dict[str, "RuntimeHandle"],
        bootstrap: "RuntimeBootstrap",
        node_runner: "RuntimeNodeRunner",
        artifacts: "RuntimeArtifacts",
        events: "RuntimeEvents",
        handle_cls: type["RuntimeHandle"],
        evaluate_acceptance_callback: Any | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.run_store = run_store
        self.history = history
        self.state_manager = state_manager
        self.active = active
        self.bootstrap = bootstrap
        self.node_runner = node_runner
        self.artifacts = artifacts
        self.events = events
        self.handle_cls = handle_cls
        self.evaluate_acceptance_callback = evaluate_acceptance_callback

    def list_active_run_ids(self) -> list[str]:
        return sorted(self.active.keys())

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

    def retained_work_entries(self, run: Any) -> list[dict[str, Any]]:
        retained = (
            run.metadata.get("retained_work", [])
            if isinstance(run.metadata.get("retained_work"), list)
            else []
        )
        return [item for item in retained if isinstance(item, dict)]

    def requeue_unblocked_nodes(self, handle: "RuntimeHandle") -> None:
        for node in handle.runtime.nodes:
            node_state = handle.run.nodes.get(node.id)
            if not node_state or node_state.status != "blocked":
                continue
            if all(
                handle.run.nodes.get(dep)
                and handle.run.nodes[dep].status == "succeeded"
                for dep in node.depends_on
            ):
                node_state.status = "queued"

    def recompute_run_status(self, handle: "RuntimeHandle") -> str:
        self.requeue_unblocked_nodes(handle)
        statuses = [node.status for node in handle.run.nodes.values()]
        if any(status == "failed" for status in statuses):
            return "failed"
        if any(status in {"queued", "blocked", "running"} for status in statuses):
            return "paused"
        return "succeeded"

    def mark_phase_integrated_nodes_succeeded(
        self,
        *,
        handle: "RuntimeHandle",
        phase: str,
        integration: dict[str, Any],
    ) -> None:
        for node in handle.runtime.nodes:
            if node.phase != phase or node.role != "orchestrator":
                continue
            node_state = handle.run.nodes.get(node.id)
            if not node_state or node_state.status not in {"queued", "blocked"}:
                continue
            if not all(
                handle.run.nodes.get(dep)
                and handle.run.nodes[dep].status == "succeeded"
                for dep in node.depends_on
            ):
                continue
            node_state.status = "succeeded"
            node_state.result = {
                "mode": "phase_integrated_from_salvage",
                "integration": integration,
            }

    def build_node_reconciliation_rows(
        self,
        *,
        handle: "RuntimeHandle",
        git_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        retained_by_node = {
            str(item.get("node_id") or ""): item
            for item in self.retained_work_entries(handle.run)
            if str(item.get("node_id") or "").strip()
        }
        phases = git_state.get("phases", {}) if isinstance(git_state, dict) else {}
        rows: list[dict[str, Any]] = []
        for node in handle.runtime.nodes:
            persisted = handle.run.nodes.get(node.id)
            phase_state = (
                phases.get(node.phase, {})
                if isinstance(phases.get(node.phase, {}), dict)
                else {}
            )
            workers = (
                phase_state.get("workers", {})
                if isinstance(phase_state.get("workers", {}), dict)
                else {}
            )
            worker_state = (
                workers.get(node.id, {})
                if isinstance(workers.get(node.id, {}), dict)
                else {}
            )
            retained = retained_by_node.get(node.id, {})
            phase_integrated = bool(phase_state.get("integrated_to_base"))
            merged_workers = {
                str(item)
                for item in phase_state.get("merged_workers", [])
                if str(item).strip()
            }
            worker_branch = str(
                worker_state.get("branch") or retained.get("branch") or ""
            ).strip()
            worktree_path = str(
                worker_state.get("worktree_path") or retained.get("worktree_path") or ""
            ).strip()
            worktree_exists = bool(retained.get("worktree_exists"))
            if worktree_path:
                worktree_exists = Path(worktree_path).expanduser().exists()
            committed = bool(worker_state.get("committed")) or bool(
                retained.get("committed")
            )
            commit = str(retained.get("commit") or "").strip()
            if commit:
                committed = True
            derived_status = persisted.status if persisted else "queued"
            if node.role == "worker":
                if phase_integrated:
                    derived_status = "merged_to_base"
                elif worker_branch and worker_branch in merged_workers:
                    derived_status = "merged_to_phase"
                elif committed:
                    derived_status = "committed_worker"
                elif retained and worktree_exists:
                    derived_status = "dirty_salvage_present"
                elif worktree_path and worktree_exists:
                    derived_status = "prepared_worktree_present"
            rows.append(
                {
                    "node_id": node.id,
                    "phase": node.phase,
                    "role": node.role,
                    "persisted_status": persisted.status if persisted else "missing",
                    "derived_status": derived_status,
                    "branch": worker_branch,
                    "commit": commit,
                    "worktree_path": worktree_path,
                    "retained": bool(retained),
                    "worktree_exists": worktree_exists,
                    "repair_action": "",
                }
            )
        return rows

    def apply_reconciled_state(
        self,
        *,
        handle: "RuntimeHandle",
        checkpoint: Any,
        node_rows: list[dict[str, Any]],
        phase_rows: list[dict[str, Any]],
        git_state: dict[str, Any],
    ) -> list[str]:
        issues: list[str] = []
        row_by_id = {
            str(row.get("node_id") or ""): row
            for row in node_rows
            if isinstance(row, dict)
        }
        for node in handle.runtime.nodes:
            persisted = handle.run.nodes.get(node.id)
            row = row_by_id.get(node.id, {})
            if persisted is None or not isinstance(row, dict):
                continue
            derived = str(row.get("derived_status") or persisted.status)
            repair_action = ""
            if derived in {"merged_to_base", "merged_to_phase"}:
                persisted.status = "succeeded"
                repair_action = "mark_succeeded_from_git_merge"
            elif derived == "committed_worker" and persisted.status in {
                "failed",
                "blocked",
            }:
                persisted.status = "queued"
                repair_action = "requeue_from_committed_worker"
            elif (
                derived in {"dirty_salvage_present", "prepared_worktree_present"}
                and persisted.status == "blocked"
            ):
                persisted.status = "queued"
                repair_action = f"requeue_from_{derived}"
            if repair_action:
                row["persisted_status"] = persisted.status
                row["repair_action"] = repair_action

        self.requeue_unblocked_nodes(handle)
        phase_done = (
            [
                str(item)
                for item in handle.run.metadata.get("phase_done", [])
                if str(item).strip()
            ]
            if isinstance(handle.run.metadata.get("phase_done"), list)
            else []
        )
        derived_complete = {
            str(row.get("phase") or "")
            for row in phase_rows
            if bool(row.get("derived_complete"))
        }
        handle.run.metadata["phase_done"] = [
            phase for phase in phase_done if phase in derived_complete
        ]
        handle.run.metadata["git_state"] = git_state
        handle.run.metadata["retained_work"] = list(git_state.get("retained_work", []))
        handle.run.metadata["reconciled_at"] = datetime.now(timezone.utc).isoformat()
        handle.run.metadata["derived_from_git"] = True
        handle.run.metadata["reconciliation_issues"] = issues
        handle.run.status = self.recompute_run_status(handle)
        if checkpoint is not None:
            checkpoint.node_statuses = {
                node_id: state.status for node_id, state in handle.run.nodes.items()
            }
            checkpoint.node_attempts = {
                node_id: int(state.attempt_count or 0)
                for node_id, state in handle.run.nodes.items()
            }
            checkpoint.git_state = git_state
            self.run_store.write_checkpoint(checkpoint)
        self.state_manager.persist_runtime_state(handle, handle.run.status)
        return issues

    def reconcile_run(self, run_id: str, *, apply: bool = False) -> dict[str, Any]:
        self.bootstrap.require_git_repository()
        state = self.run_store.load_state(run_id)
        checkpoint = self.run_store.load_checkpoint(run_id)
        manager = GitWorktreeManager(self.workspace_root, run_id)
        inventory = manager.managed_artifact_inventory(run_id)
        has_artifacts = bool(inventory.get("branches") or inventory.get("worktrees"))
        if state is None:
            return {
                "ok": has_artifacts,
                "run_id": run_id,
                "state_missing": True,
                "status": "missing",
                "checkpoint_status": "",
                "plan_path": "",
                "git_reconciliation": {
                    "preserved_paths": [],
                    "preserved_branches": [],
                    "retained_work": [],
                },
                "inventory": inventory,
                "retained_work": [],
                "nodes": [],
            }

        if run_id not in self.active and not self.recover_run(run_id):
            return {
                "ok": False,
                "run_id": run_id,
                "state_missing": False,
                "status": state.status,
                "checkpoint_status": checkpoint.status if checkpoint else "",
                "plan_path": state.plan_path,
                "inventory": inventory,
                "retained_work": [],
                "nodes": [],
                "issues": ["run could not be materialized for reconciliation"],
            }

        handle = self.active[run_id]
        git_state = manager.bootstrap_state(handle.run.metadata.get("git_state"))
        handle.run.metadata["git_state"] = git_state
        git_reconciliation = manager.reconcile_state(git_state)
        retained_work = list(git_state.get("retained_work", []))
        handle.run.metadata["retained_work"] = retained_work
        handle.run.metadata["git_reconciliation"] = git_reconciliation
        node_rows = self.build_node_reconciliation_rows(
            handle=handle, git_state=git_state
        )
        phase_rows: list[dict[str, Any]] = []
        phases = git_state.get("phases", {}) if isinstance(git_state, dict) else {}
        for phase, phase_state in phases.items():
            if not isinstance(phase_state, dict):
                continue
            phase_nodes = [row for row in node_rows if row["phase"] == phase]
            phase_rows.append(
                {
                    "phase": str(phase),
                    "integrated_to_base": bool(phase_state.get("integrated_to_base")),
                    "merged_workers": len(phase_state.get("merged_workers", [])),
                    "node_count": len(phase_nodes),
                    "derived_complete": bool(phase_nodes)
                    and all(
                        row["derived_status"]
                        in {"succeeded", "merged_to_phase", "merged_to_base"}
                        for row in phase_nodes
                    ),
                }
            )

        issues: list[str] = []
        if apply:
            issues.extend(
                self.apply_reconciled_state(
                    handle=handle,
                    checkpoint=checkpoint,
                    node_rows=node_rows,
                    phase_rows=phase_rows,
                    git_state=git_state,
                )
            )

        persist_status = (
            handle.run.status
            if apply
            else "paused_recovery_required"
            if handle.run.status == "paused_recovery_required"
            else "paused"
        )
        self.state_manager.persist_runtime_state(handle, persist_status)
        return {
            "ok": True,
            "run_id": run_id,
            "state_missing": False,
            "status": handle.run.status,
            "checkpoint_status": checkpoint.status if checkpoint else "",
            "plan_path": handle.run.plan_path,
            "git_reconciliation": git_reconciliation,
            "inventory": inventory,
            "retained_work": retained_work,
            "nodes": node_rows,
            "phases": phase_rows,
            "issues": issues,
            "applied": apply,
        }

    def promote_salvage(self, run_id: str, node_id: str) -> tuple[bool, dict[str, Any]]:
        self.bootstrap.require_git_repository()
        if run_id not in self.active and not self.recover_run(run_id):
            return False, {
                "reason": "run_not_found",
                "error": "run not found or unrecoverable",
            }

        handle = self.active[run_id]
        target_node = next(
            (node for node in handle.runtime.nodes if node.id == node_id),
            None,
        )
        if target_node is None:
            return False, {
                "reason": "node_not_found",
                "error": f"node '{node_id}' was not found in the recovered runtime",
            }
        if target_node.role != "worker":
            return False, {
                "reason": "salvage_not_promotable",
                "error": "only retained worker nodes can be promoted",
            }

        retained_entry = next(
            (
                item
                for item in self.retained_work_entries(handle.run)
                if str(item.get("node_id") or "") == node_id
            ),
            None,
        )
        if retained_entry is None:
            return False, {
                "reason": "salvage_not_found",
                "error": f"no retained work was found for node '{node_id}'",
            }

        branch = str(retained_entry.get("branch") or "").strip()
        commit = str(retained_entry.get("commit") or "").strip()
        worktree_path = str(retained_entry.get("worktree_path") or "").strip()
        committed = bool(retained_entry.get("committed")) or bool(commit)
        if not branch or not worktree_path:
            return False, {
                "reason": "salvage_not_promotable",
                "error": "retained work must include a branch and worktree path",
            }

        worktree = Path(worktree_path).expanduser().resolve()
        if not worktree.exists():
            return False, {
                "reason": "salvage_not_promotable",
                "error": f"retained worktree is unavailable: {worktree}",
            }

        git_manager = GitWorktreeManager(self.workspace_root, run_id)
        git_state = git_manager.bootstrap_state(handle.run.metadata.get("git_state"))
        handle.run.metadata["git_state"] = git_state
        phase_state = git_manager.prepare_phase(git_state, target_node.phase)
        workers = phase_state.setdefault("workers", {})
        worker_state = workers.setdefault(target_node.id, {})
        worker_state["branch"] = branch
        worker_state["worktree_path"] = str(worktree)
        worker_state["committed"] = True
        if str(worktree) not in git_state.get("cleanup_paths", []):
            git_state.setdefault("cleanup_paths", []).append(str(worktree))
        if branch not in git_state.get("cleanup_branches", []):
            git_state.setdefault("cleanup_branches", []).append(branch)

        evaluate_acceptance = (
            self.evaluate_acceptance_callback
            or self.node_runner.evaluate_acceptance_impl
        )
        acceptance_ok, acceptance_result = evaluate_acceptance(
            target_node,
            {"worktree": str(worktree), "branch": branch, "commit": commit},
            timeout_seconds=int(handle.plan.constraints.acceptance_timeout_seconds),
        )
        if not acceptance_ok:
            return False, acceptance_result

        if not committed:
            add = git_manager._git(["add", "-A"], cwd=worktree, check=False)  # noqa: SLF001
            if add.returncode != 0:
                return False, {
                    "reason": "salvage_not_promotable",
                    "error": add.stderr.strip()
                    or add.stdout.strip()
                    or "unable to stage salvaged work",
                }
            commit_result = git_manager._git(  # noqa: SLF001
                [
                    "commit",
                    "--allow-empty",
                    "-m",
                    f"salvage({target_node.source_task_id or node_id}): promote retained work",
                ],
                cwd=worktree,
                check=False,
            )
            if commit_result.returncode != 0:
                return False, {
                    "reason": "salvage_not_promotable",
                    "error": commit_result.stderr.strip()
                    or commit_result.stdout.strip()
                    or "unable to commit salvaged work",
                }
            commit_meta = git_manager.inspect_managed_target(
                worktree_path=str(worktree), branch=branch
            )
            commit = str(commit_meta.get("commit") or "").strip()
            committed = bool(commit)
            worker_state["committed"] = committed

        status, integration = git_manager.integrate_phase(
            git_state,
            target_node.phase,
            ignore_overlap_paths=self.node_runner.integration_overlap_ignore_paths(
                handle
            ),
        )
        if status != "success":
            return False, integration

        retained_work = [
            item
            for item in self.retained_work_entries(handle.run)
            if str(item.get("node_id") or "") != node_id
        ]
        git_state["retained_work"] = retained_work
        handle.run.metadata["retained_work"] = retained_work
        handle.run.metadata["git_reconciliation"] = git_manager.reconcile_state(
            git_state
        )
        handle.run.metadata["reconciled_at"] = datetime.now(timezone.utc).isoformat()
        handle.run.metadata["derived_from_git"] = True

        node_state = handle.run.nodes.get(node_id)
        if node_state is not None:
            node_state.status = "succeeded"
            node_state.result = {
                "mode": "salvage_promoted",
                "reason": "salvage_promoted",
                "worktree": {
                    "branch": branch,
                    "worktree": str(worktree),
                    "commit": commit,
                },
                "acceptance": acceptance_result,
                "integration": integration,
            }

        self.mark_phase_integrated_nodes_succeeded(
            handle=handle,
            phase=target_node.phase,
            integration=integration,
        )
        handle.run.status = self.recompute_run_status(handle)
        self.state_manager.persist_runtime_state(handle, handle.run.status)
        self.history.upsert(handle.run)
        self.artifacts.write_artifacts(handle.run)
        return True, {
            "run_status": handle.run.status,
            "node_id": node_id,
            "branch": branch,
            "commit": commit,
            "acceptance": acceptance_result,
            "integration": integration,
            "retained_count": len(retained_work),
        }

    def recover_run(self, run_id: str) -> bool:
        self.bootstrap.require_git_repository()
        if run_id in self.active:
            return True

        handle = self.bootstrap.rebuild_handle_for_recovery(
            handle_cls=self.handle_cls, run_id=run_id
        )
        if handle is None:
            return False
        self.reconcile_run(run_id, apply=True)
        self.state_manager.persist_runtime_state(
            handle,
            "paused_recovery_required"
            if handle.run.status == "paused_recovery_required"
            else "paused",
        )
        return True

    def file_has_conflict_markers(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
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
                if path.exists() and self.file_has_conflict_markers(path):
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
        self.events.emit(
            handle,
            stage="orchestrator",
            event="RECOVERY_MODE_SELECTED",
            level="warn",
            message=f"recovery mode selected: {mode}",
            meta={"mode": mode, "has_prompt": bool(prompt)},
        )
        self.state_manager.checkpoint(handle, status="paused_recovery_required")
        return True
