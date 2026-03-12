from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile

import pytest
from ralphite.engine import LocalOrchestrator
from ralphite.engine.git_worktree import GitRequiredError, GitWorktreeManager
from ralphite.engine.models import (
    NodeRuntimeState,
    RunCheckpoint,
    RunPersistenceState,
    RunViewState,
)

_AGENT_DEFAULTS = """\
version: 1
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
behaviors:
  - id: prepare_dispatch_default
    kind: prepare_dispatch
    agent: orchestrator_default
    enabled: true
  - id: merge_default
    kind: merge_and_conflict_resolution
    agent: orchestrator_default
    enabled: true
  - id: summarize_default
    kind: summarize_work
    agent: orchestrator_default
    enabled: true
"""


def _init_repo(path: Path) -> None:
    (path / "agent_defaults.yaml").write_text(_AGENT_DEFAULTS, encoding="utf-8")
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


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    _init_repo(tmp_path)
    return tmp_path


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


def test_recover_run_and_resume_from_checkpoint(workspace: Path) -> None:
    run_id = "recover-me"
    run = _build_stub_run(workspace, run_id)

    orch = LocalOrchestrator(workspace)
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

    orch2 = LocalOrchestrator(workspace)
    assert run_id in orch2.list_recoverable_runs()
    assert orch2.recover_run(run_id) is True
    assert orch2.resume_from_checkpoint(run_id) is True

    assert orch2.wait_for_run(run_id, timeout=8.0) is True
    recovered = orch2.get_run(run_id)
    assert recovered is not None
    assert recovered.status in {"succeeded", "failed", "cancelled"}
    assert any(evt.get("event") == "RUN_DONE" for evt in recovered.events)


def test_recovery_retries_run_state_write_when_replace_is_temporarily_locked(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "recover-write-contention"
    run = _build_stub_run(workspace, run_id)

    orch = LocalOrchestrator(workspace)
    orch.run_store.acquire_lock(run_id)
    lock_path = orch.run_store.run_dir(run_id) / "lock"
    lock_path.write_text(
        '{"pid": 999999, "acquired_at": "2026-01-01T00:00:00Z"}', encoding="utf-8"
    )
    orch.run_store.write_state(
        RunPersistenceState(
            run_id=run_id,
            status="running",
            plan_path=run.plan_path,
            run=run,
            loop_counts={"main_loop": 0},
            last_seq=1,
        )
    )

    original_replace = Path.replace
    attempts = {"count": 0}

    def flaky_replace(self: Path, target: Path) -> Path:
        if self.name.startswith("run_state.json.") and attempts["count"] == 0:
            attempts["count"] += 1
            raise PermissionError("simulated transient run_state.json lock")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    orch2 = LocalOrchestrator(workspace)
    recovered = orch2.run_store.load_state(run_id)
    assert attempts["count"] == 1
    assert recovered is not None
    assert recovered.status == "paused"


def test_recovery_preflight_blocks_unresolved_conflict_markers(workspace: Path) -> None:
    orch = LocalOrchestrator(workspace)
    plan_path = orch.goal_to_plan("recovery preflight")
    run_id = orch.start_run(plan_ref=str(plan_path))
    assert orch.wait_for_run(run_id, timeout=8.0) is True

    assert orch.recover_run(run_id) is True
    assert orch.set_recovery_mode(run_id, "manual") is True

    conflict_file = workspace / "conflict.txt"
    conflict_file.write_text(
        "<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n", encoding="utf-8"
    )

    handle = orch.active[run_id]  # noqa: SLF001
    handle.run.status = "paused_recovery_required"
    handle.run.metadata.setdefault("recovery", {})
    handle.run.metadata["recovery"]["details"] = {
        "worktree": str(workspace),
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


def test_recover_run_reconciles_checkpoint_git_state(workspace: Path) -> None:
    run_id = "recover-retained"
    run = _build_stub_run(workspace, run_id)
    orch = LocalOrchestrator(workspace)
    manager = GitWorktreeManager(workspace, run_id)
    git_state = manager.bootstrap_state()
    runtime_node_id = next(iter(run.nodes.keys()))
    worker = manager.prepare_worker(git_state, "phase-1", runtime_node_id)
    manager.retain_target(
        git_state,
        scope="worker",
        reason="backend_nonzero",
        phase="phase-1",
        node_id=runtime_node_id,
        worktree_path=str(worker.get("worktree_path") or ""),
        branch=str(worker.get("branch") or ""),
        committed=False,
    )
    run.metadata["git_state"] = git_state

    state = RunPersistenceState(
        run_id=run_id,
        status="running",
        plan_path=run.plan_path,
        run=run,
        loop_counts={"main_loop": 0},
        last_seq=1,
    )
    orch.run_store.write_state(state)
    orch.run_store.write_checkpoint(
        RunCheckpoint(
            run_id=run_id,
            status="running",
            plan_path=run.plan_path,
            last_seq=1,
            loop_counts={"main_loop": 0},
            retry_count=1,
            node_attempts={runtime_node_id: 2},
            node_statuses={runtime_node_id: "failed"},
            git_state=git_state,
        )
    )
    lock_path = orch.run_store.run_dir(run_id) / "lock"
    lock_path.write_text(
        '{"pid": 999999, "acquired_at": "2026-01-01T00:00:00Z"}', encoding="utf-8"
    )

    orch2 = LocalOrchestrator(workspace)
    assert orch2.recover_run(run_id) is True
    recovered = orch2.get_run(run_id)
    assert recovered is not None
    recovered_node = recovered.nodes[runtime_node_id]
    assert recovered_node.status == "queued"
    assert recovered_node.attempt_count == 2
    retained = (
        recovered.metadata.get("retained_work", [])
        if isinstance(recovered.metadata.get("retained_work"), list)
        else []
    )
    assert retained
    reconciliation = recovered.metadata.get("git_reconciliation", {})
    assert isinstance(reconciliation, dict)
    assert reconciliation.get("preserved_paths")


def test_recover_run_requires_git_workspace(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralphite.engine import LocalOrchestrator

    monkeypatch.undo()
    plain = Path(tempfile.mkdtemp())
    orch = LocalOrchestrator(plain)
    with pytest.raises(GitRequiredError):
        orch.recover_run("missing-run")
