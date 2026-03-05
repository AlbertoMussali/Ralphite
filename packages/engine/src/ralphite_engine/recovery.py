from __future__ import annotations

from typing import Iterable

from ralphite_engine.models import RunCheckpoint, RunPersistenceState, RunViewState


def recoverable_run_ids(
    states: Iterable[RunPersistenceState], *, lock_is_stale: callable
) -> list[str]:
    recoverable: list[str] = []
    active_states = {
        "running",
        "checkpointing",
        "recovering",
        "paused",
        "paused_recovery_required",
    }
    for state in states:
        if state.status not in active_states:
            continue
        if state.status in {"recovering", "paused"}:
            recoverable.append(state.run_id)
            continue
        if lock_is_stale(state.run_id):
            recoverable.append(state.run_id)
    return sorted(set(recoverable))


def to_paused_for_recovery(
    state: RunPersistenceState, checkpoint: RunCheckpoint | None
) -> RunPersistenceState:
    run = RunViewState.model_validate(state.run.model_dump())
    for node in run.nodes.values():
        if node.status == "running":
            node.status = "queued"
    run.status = (
        "paused_recovery_required"
        if state.status == "paused_recovery_required"
        else "paused"
    )
    run.active_node_id = None

    loop_counts = dict(state.loop_counts)
    if checkpoint is not None:
        loop_counts.update(checkpoint.loop_counts)

    return RunPersistenceState(
        run_id=state.run_id,
        status="paused",
        plan_path=state.plan_path,
        run=run,
        loop_counts=loop_counts,
        last_seq=max(state.last_seq, checkpoint.last_seq if checkpoint else 0),
    )
