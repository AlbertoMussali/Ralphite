from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ralphite_engine import LocalOrchestrator
from ralphite_engine.models import NodeRuntimeState, RunCheckpoint, RunPersistenceState, RunViewState


def _build_stub_run(workspace: Path, run_id: str) -> RunViewState:
    orch = LocalOrchestrator(workspace)
    plan_path = orch.goal_to_plan("Recover this run")

    # Use starter plan nodes for realistic replay state.
    from ralphite_engine.validation import parse_plan_yaml

    plan_model = parse_plan_yaml(plan_path.read_text(encoding="utf-8"))
    nodes = {
        node.id: NodeRuntimeState(
            node_id=node.id,
            kind=node.kind.value,
            group=node.group,
            status="queued",
            depends_on=list(node.depends_on),
        )
        for node in plan_model.graph.nodes
    }

    return RunViewState(
        id=run_id,
        plan_path=str(plan_path),
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        nodes=nodes,
        metadata={"permission_snapshot": orch.default_permission_snapshot()},
    )


def test_recover_run_and_resume_from_checkpoint(tmp_path: Path) -> None:
    run_id = "recover-me"
    run = _build_stub_run(tmp_path, run_id)

    orch = LocalOrchestrator(tmp_path)
    orch.run_store.acquire_lock(run_id)
    lock_path = orch.run_store.run_dir(run_id) / "lock"
    lock_path.write_text('{"pid": 999999, "acquired_at": "2026-01-01T00:00:00Z"}', encoding="utf-8")

    state = RunPersistenceState(
        run_id=run_id,
        status="running",
        plan_path=run.plan_path,
        run=run,
        loop_counts={"main_loop": 0},
        last_seq=1,
    )
    orch.run_store.write_state(state)
    orch.run_store.append_event(
        run_id,
        {
            "id": 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "stage": "plan",
            "event": "RUN_STARTED",
            "level": "info",
            "message": "run started",
            "meta": {},
        },
    )
    orch.run_store.write_checkpoint(
        RunCheckpoint(
            run_id=run_id,
            status="running",
            plan_path=run.plan_path,
            last_seq=1,
            loop_counts={"main_loop": 0},
            retry_count=0,
            node_attempts={key: 0 for key in run.nodes.keys()},
            node_statuses={key: "queued" for key in run.nodes.keys()},
        )
    )

    orch2 = LocalOrchestrator(tmp_path)
    assert run_id in orch2.list_recoverable_runs()
    assert orch2.recover_run(run_id) is True
    assert orch2.resume_from_checkpoint(run_id) is True

    assert orch2.wait_for_run(run_id, timeout=8.0) is True
    recovered = orch2.get_run(run_id)
    assert recovered is not None
    assert recovered.status in {"succeeded", "failed", "cancelled"}
    assert any(evt.get("event") == "RUN_DONE" for evt in recovered.events)
