from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ralphite.engine.models import RunCheckpoint, RunPersistenceState, RunViewState
from ralphite.engine.recovery import recoverable_run_ids
from ralphite.engine.run_store import RunStore

if TYPE_CHECKING:
    from ralphite.engine.orchestrator import RuntimeHandle

logger = logging.getLogger(__name__)


class RunStateManager:
    """Manages the persistence, checkpoints, and history of orchestrator runs."""

    def __init__(self, run_store: RunStore, history_store: Any) -> None:
        self.run_store = run_store
        self.history = history_store

    def persist_runtime_state(self, handle: "RuntimeHandle", status: str) -> None:
        """Persist the current runtime state to the store and history."""
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

    def checkpoint(self, handle: "RuntimeHandle", status: str = "running") -> None:
        """Create a checkpoint of the current run."""
        self.persist_runtime_state(handle, "checkpointing")
        checkpoint = RunCheckpoint(
            run_id=handle.run.id,
            status=status,
            plan_path=handle.run.plan_path,
            last_seq=handle.seq,
            loop_counts={},
            retry_count=handle.run.retry_count,
            node_attempts={
                node_id: node.attempt_count
                for node_id, node in handle.run.nodes.items()
            },
            node_statuses={
                node_id: node.status for node_id, node in handle.run.nodes.items()
            },
            active_node_id=handle.run.active_node_id,
            git_state=(
                handle.run.metadata.get("git_state", {})
                if isinstance(handle.run.metadata.get("git_state"), dict)
                else {}
            ),
        )
        self.run_store.write_checkpoint(checkpoint)
        self.persist_runtime_state(handle, status)

    def load_run_state(self, run_id: str) -> RunViewState | None:
        """Load a run view state from the store."""
        state = self.run_store.load_state(run_id)
        return state.run if state else None

    def list_recoverable_runs(self) -> list[str]:
        """List the IDs of runs that can be recovered."""
        states = [
            state
            for run_id in self.run_store.list_run_ids()
            if (state := self.run_store.load_state(run_id)) is not None
        ]
        return recoverable_run_ids(states, lock_is_stale=self.run_store.lock_is_stale)
