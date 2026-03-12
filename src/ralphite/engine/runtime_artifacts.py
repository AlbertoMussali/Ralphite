from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ralphite.engine.models import ArtifactIndex, RunMetrics, RunViewState
from ralphite.engine.reporting import build_final_report
from ralphite.engine.task_writer import mark_tasks_completed

if TYPE_CHECKING:
    from ralphite.engine.git_worktree import GitWorktreeManager
    from ralphite.engine.orchestrator import RuntimeHandle
    from ralphite.engine.runtime_events import RuntimeEvents
    from ralphite.engine.run_store import RunStore
    from ralphite.engine.state_manager import RunStateManager


class RuntimeArtifacts:
    def __init__(
        self,
        *,
        paths: dict[str, Path],
        run_store: "RunStore",
        state_manager: "RunStateManager",
        events: "RuntimeEvents",
        writeback_target: Any,
    ) -> None:
        self.paths = paths
        self.run_store = run_store
        self.state_manager = state_manager
        self.events = events
        self.writeback_target = writeback_target

    def build_run_metrics(
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

    def write_artifacts(self, run: RunViewState) -> ArtifactIndex:
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
        salvage_path = artifacts_dir / "salvage_bundle.json"
        report = build_final_report(
            run,
            artifact_paths={
                "final_report": str(report_path),
                "run_metrics": str(metrics_path),
                "machine_bundle": str(bundle_path),
                "salvage_bundle": str(salvage_path),
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
            "nodes": {
                key: value.model_dump(mode="json") for key, value in run.nodes.items()
            },
            "metadata": run.metadata,
            "metrics": metrics_payload,
        }
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

        salvage_bundle = {
            "run_id": run.id,
            "status": run.status,
            "retained_work": (
                run.metadata.get("retained_work", [])
                if isinstance(run.metadata.get("retained_work"), list)
                else []
            ),
            "cleanup_decision": (
                run.metadata.get("cleanup_decision", {})
                if isinstance(run.metadata.get("cleanup_decision"), dict)
                else {}
            ),
            "git_reconciliation": (
                run.metadata.get("git_reconciliation", {})
                if isinstance(run.metadata.get("git_reconciliation"), dict)
                else {}
            ),
        }
        salvage_path.write_text(json.dumps(salvage_bundle, indent=2), encoding="utf-8")

        items = [
            {"id": "final_report", "path": str(report_path), "format": "markdown"},
            {"id": "run_metrics", "path": str(metrics_path), "format": "json"},
            {"id": "machine_bundle", "path": str(bundle_path), "format": "json"},
            {"id": "salvage_bundle", "path": str(salvage_path), "format": "json"},
        ]
        run.artifacts = items
        return ArtifactIndex(
            run_id=run.id, artifacts_dir=str(artifacts_dir), items=items
        )

    def successful_task_ids(self, handle: "RuntimeHandle") -> list[str]:
        task_ids: list[str] = []
        for node in handle.runtime.nodes:
            if node.role != "worker" or not node.source_task_id:
                continue
            node_state = handle.run.nodes.get(node.id)
            if node_state and node_state.status == "succeeded":
                task_ids.append(node.source_task_id)
        return sorted(dict.fromkeys(task_ids))

    def writeback_tasks(
        self,
        *,
        handle: "RuntimeHandle",
        git_manager: "GitWorktreeManager",
    ) -> dict[str, Any]:
        task_file = Path(handle.run.plan_path)
        task_ids = self.successful_task_ids(handle)
        writeback_mode, writeback_target = self.writeback_target(task_file, handle.plan)

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

    def prepare_terminal_artifacts(
        self, handle: "RuntimeHandle", git_manager: "GitWorktreeManager"
    ) -> None:
        run = handle.run
        if run.status == "succeeded":
            writeback = self.writeback_tasks(handle=handle, git_manager=git_manager)
            error = writeback.get("error") if isinstance(writeback, dict) else None
            if isinstance(error, dict):
                run.status = "failed"
                self.events.emit(
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
        self.write_artifacts(run)

    def finish_terminal_run(self, handle: "RuntimeHandle") -> None:
        run = handle.run
        self.events.emit(
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
        self.state_manager.persist_runtime_state(handle, run.status)
        self.run_store.release_lock(run.id)
        handle.finished_event.set()

    def finalize_terminal_run(
        self, handle: "RuntimeHandle", git_manager: "GitWorktreeManager"
    ) -> None:
        self.prepare_terminal_artifacts(handle, git_manager)
        self.finish_terminal_run(handle)
