from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile

import pytest
from ralphite.engine import LocalOrchestrator
from ralphite.engine.git_worktree import GitRequiredError
from ralphite.engine.models import (
    NodeRuntimeState,
    RunCheckpoint,
    RunPersistenceState,
    RunViewState,
)


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ralphite Test"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ralphite@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _git_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)


def _build_stub_run(workspace: Path, run_id: str) -> RunViewState:
    orch = LocalOrchestrator(workspace)
    plan_path = orch.goal_to_plan("Recover this run")

    # Use starter plan nodes for realistic replay state.
    from ralphite.engine.validation import parse_plan_yaml

    plan_document = parse_plan_yaml(plan_path.read_text(encoding="utf-8"))
    runtime, _meta = orch._materialize_runtime_plan(plan_document)  # noqa: SLF001
    nodes = {
        node.id: NodeRuntimeState(
            node_id=node.id,
            kind=node.kind,
            group=node.group,
            status="queued",
            depends_on=list(node.depends_on),
        )
        for node in runtime.nodes
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
    lock_path.write_text(
        '{"pid": 999999, "acquired_at": "2026-01-01T00:00:00Z"}', encoding="utf-8"
    )

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


def test_recovery_preflight_blocks_unresolved_conflict_markers(tmp_path: Path) -> None:
    orch = LocalOrchestrator(tmp_path)
    plan_path = orch.goal_to_plan("recovery preflight")
    run_id = orch.start_run(plan_ref=str(plan_path))
    assert orch.wait_for_run(run_id, timeout=8.0) is True

    assert orch.recover_run(run_id) is True
    assert orch.set_recovery_mode(run_id, "manual") is True

    conflict_file = tmp_path / "conflict.txt"
    conflict_file.write_text(
        "<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n", encoding="utf-8"
    )

    handle = orch.active[run_id]  # noqa: SLF001
    handle.run.status = "paused_recovery_required"
    handle.run.metadata.setdefault("recovery", {})
    handle.run.metadata["recovery"]["details"] = {
        "worktree": str(tmp_path),
        "conflict_files": ["conflict.txt"],
        "next_commands": ["resolve conflict"],
    }

    preflight = orch.recovery_preflight(run_id)
    assert preflight.get("ok") is False
    assert "conflict.txt" in preflight.get("unresolved_conflict_files", [])
    assert orch.resume_from_checkpoint(run_id) is False

    run = orch.get_run(run_id)
    assert run is not None
    assert run.metadata.get("recovery", {}).get("status") == "preflight_failed"


def test_recover_run_requires_git_workspace(tmp_path: Path) -> None:
    plain = Path(tempfile.mkdtemp())
    orch = LocalOrchestrator(plain)
    with pytest.raises(GitRequiredError):
        orch.recover_run("missing-run")
