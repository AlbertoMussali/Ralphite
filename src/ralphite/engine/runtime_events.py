from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ralphite.engine.event_logger import RunEventLogger

if TYPE_CHECKING:
    from ralphite.engine.orchestrator import RuntimeHandle
    from ralphite.engine.structure_compiler import RuntimeNodeSpec


class RuntimeEvents:
    def __init__(self, event_logger: RunEventLogger) -> None:
        self.event_logger = event_logger

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

    def emit_node_started(
        self, handle: "RuntimeHandle", node: "RuntimeNodeSpec"
    ) -> None:
        self.event_logger.emit_node_started(handle, node)

    def emit_node_completed(
        self, handle: "RuntimeHandle", node: "RuntimeNodeSpec", success: bool
    ) -> None:
        self.event_logger.emit_node_completed(handle, node, success)

    def record_interruption_reason(self, handle: "RuntimeHandle", reason: str) -> None:
        normalized = str(reason or "").strip()
        if not normalized:
            return
        reasons = handle.run.metadata.setdefault("interruption_reasons", [])
        if isinstance(reasons, list):
            reasons.append(normalized)
