from __future__ import annotations

import logging
from datetime import datetime, timezone
from queue import Empty
from typing import TYPE_CHECKING, Any, Generator

if TYPE_CHECKING:
    from ralphite.engine.orchestrator import RuntimeHandle
    from ralphite.schemas.validation import RuntimeNodeSpec
    from ralphite.engine.run_store import RunStore

logger = logging.getLogger(__name__)


class RunEventLogger:
    """Manages event emission and streaming for orchestrator runs."""

    def __init__(
        self,
        run_store: "RunStore",
        history_manager: Any,
        active_runs: dict[str, "RuntimeHandle"],
    ) -> None:
        self.run_store = run_store
        self.history = history_manager
        self.active_runs = active_runs

    def emit(
        self,
        handle: "RuntimeHandle",
        *,
        stage: str,
        event: str,
        level: str,
        message: str,
        group: str | None = None,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event, append to the run's journal and history, and queue for streaming."""
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

    def emit_node_started(
        self, handle: "RuntimeHandle", node: "RuntimeNodeSpec"
    ) -> None:
        """Emit multiple structured events for node start lifecycle."""
        metadata = handle.run.metadata
        phase = node.phase
        lane = node.lane

        phase_started = set(metadata.get("phase_started", []))
        if phase and phase not in phase_started:
            self.emit(
                handle,
                stage="plan",
                event="PHASE_STARTED",
                level="info",
                message=f"phase started: {phase}",
                group=phase,
            )
            phase_started.add(phase)
            metadata["phase_started"] = sorted(phase_started)

        if node.role == "worker":
            lane_started = set(metadata.get("lane_started", []))
            lane_key = f"{phase}:{lane}"
            if lane_key not in lane_started:
                self.emit(
                    handle,
                    stage="plan",
                    event="LANE_STARTED",
                    level="info",
                    message=f"lane started: {lane}",
                    group=phase,
                    meta={"lane": lane},
                )
                lane_started.add(lane_key)
                metadata["lane_started"] = sorted(lane_started)
            self.emit(
                handle,
                stage="task",
                event="WORKER_STARTED",
                level="info",
                message="worker task started",
                group=phase,
                task_id=node.id,
                meta={"lane": lane},
            )
        elif node.role == "orchestrator":
            self.emit(
                handle,
                stage="orchestrator",
                event="ORCH_STARTED",
                level="info",
                message="orchestrator cell started",
                group=phase,
                task_id=node.id,
                meta={
                    "cell_id": node.cell_id,
                    "behavior_kind": node.behavior_kind,
                    "behavior_id": node.behavior_id,
                },
            )

    def emit_node_completed(
        self, handle: "RuntimeHandle", node: "RuntimeNodeSpec", success: bool
    ) -> None:
        """Emit multiple structured events for node completion lifecycle."""
        metadata = handle.run.metadata
        phase = node.phase

        if node.role == "worker" and success:
            self.emit(
                handle,
                stage="task",
                event="WORKER_MERGED",
                level="info",
                message="worker output integrated to phase branch",
                group=phase,
                task_id=node.id,
                meta={"lane": node.lane},
            )
        elif node.role == "orchestrator":
            self.emit(
                handle,
                stage="orchestrator",
                event="ORCH_DONE",
                level="info" if success else "error",
                message="orchestrator cell completed"
                if success
                else "orchestrator cell failed",
                group=phase,
                task_id=node.id,
                meta={
                    "cell_id": node.cell_id,
                    "behavior_kind": node.behavior_kind,
                    "behavior_id": node.behavior_id,
                },
            )

        phase_done = set(metadata.get("phase_done", []))
        phase_node_ids = list(metadata.get("phase_nodes", {}).get(phase, []))
        if phase and phase not in phase_done and phase_node_ids:
            statuses = [
                handle.run.nodes[node_id].status
                for node_id in phase_node_ids
                if node_id in handle.run.nodes
            ]
            terminal = {"succeeded", "failed", "blocked"}
            if statuses and all(status in terminal for status in statuses):
                self.emit(
                    handle,
                    stage="summary",
                    event="PHASE_DONE",
                    level="info"
                    if all(status == "succeeded" for status in statuses)
                    else "error",
                    message=f"phase completed: {phase}",
                    group=phase,
                )
                phase_done.add(phase)
                metadata["phase_done"] = sorted(phase_done)

    def stream_events(
        self, run_id: str, after_seq: int = 0
    ) -> Generator[dict[str, Any], None, None]:
        """Stream events for a run, combining historic store and real-time active output."""
        handle = self.active_runs.get(run_id)
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
        """Non-blocking poll of the event queue."""
        handle = self.active_runs.get(run_id)
        if not handle:
            return []
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(handle.event_queue.get_nowait())
            except Empty:
                break
        return events
