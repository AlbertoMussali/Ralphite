from __future__ import annotations

from pathlib import Path
import subprocess

from ralphite.engine.git_worktree import GitWorktreeManager
from ralphite.engine.process_guard import managed_process_marker_path


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _init_repo(path: Path) -> None:
    _git(path, "init")
    subprocess.run(
        ["git", "branch", "-m", "main"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )
    _git(path, "config", "user.name", "Ralphite Test")
    _git(path, "config", "user.email", "ralphite@example.com")
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    (path / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "initial")


def test_git_worktree_worker_merge_and_cleanup_idempotent(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runabc123")
    state = manager.bootstrap_state()

    manager.prepare_phase(state, "phase-1")
    worker = manager.prepare_worker(state, "phase-1", "phase-1::parallel::t1")
    worker_path = Path(str(worker["worktree_path"]))
    assert worker_path.exists()

    (worker_path / "worker.txt").write_text("worker output\n", encoding="utf-8")
    ok, commit_meta = manager.commit_worker(
        state, "phase-1", "phase-1::parallel::t1", "worker commit"
    )
    assert ok is True
    assert "branch" in commit_meta

    status, merge_meta = manager.integrate_phase(state, "phase-1")
    assert status == "success", merge_meta
    assert merge_meta.get("workers")

    branches = manager.list_managed_branches(state)
    worktrees = manager.list_managed_worktrees(state)
    assert branches
    assert worktrees

    cleanup_first = manager.cleanup_all(state)
    cleanup_second = manager.cleanup_all(state)
    assert cleanup_first
    assert cleanup_second == []
    assert not (tmp_path / ".ralphite" / "worktrees" / "runabc123").exists()


def test_git_worktree_conflict_fail_closed_reports_details(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runconf123")
    state = manager.bootstrap_state()

    manager.prepare_phase(state, "phase-1")
    worker = manager.prepare_worker(state, "phase-1", "phase-1::parallel::t1")
    worker_path = Path(str(worker["worktree_path"]))
    (worker_path / "shared.txt").write_text("worker-change\n", encoding="utf-8")
    ok, _meta = manager.commit_worker(
        state, "phase-1", "phase-1::parallel::t1", "worker changes shared file"
    )
    assert ok is True

    (tmp_path / "shared.txt").write_text("base-change\n", encoding="utf-8")
    _git(tmp_path, "add", "shared.txt")
    _git(tmp_path, "commit", "-m", "base changes shared file")

    status, details = manager.integrate_phase(state, "phase-1")
    assert status == "recovery_required"
    assert details.get("reason") in {"base_merge_conflict", "worker_merge_conflict"}
    assert isinstance(details.get("conflict_files"), list)
    assert isinstance(details.get("current_run_conflict_files"), list)
    assert details.get("next_commands")


def test_pre_base_integration_check_blocks_overlapping_local_changes(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runblock123")
    state = manager.bootstrap_state()

    manager.prepare_phase(state, "phase-1")
    worker = manager.prepare_worker(state, "phase-1", "phase-1::parallel::t1")
    worker_path = Path(str(worker["worktree_path"]))
    (worker_path / "shared.txt").write_text("worker-change\n", encoding="utf-8")
    ok, _meta = manager.commit_worker(
        state, "phase-1", "phase-1::parallel::t1", "worker changes shared file"
    )
    assert ok is True

    (tmp_path / "shared.txt").write_text(
        "local change without commit\n", encoding="utf-8"
    )

    status, details = manager.integrate_phase(state, "phase-1")
    assert status == "recovery_required"
    assert details.get("reason") == "base_integration_blocked_by_local_changes"
    assert "shared.txt" in details.get("overlap_files", [])


def test_cleanup_phase_prunes_state_after_successful_merge(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runclean123")
    state = manager.bootstrap_state()

    manager.prepare_phase(state, "phase-1")
    worker = manager.prepare_worker(state, "phase-1", "phase-1::parallel::t1")
    worker_path = Path(str(worker["worktree_path"]))
    (worker_path / "worker.txt").write_text("worker output\n", encoding="utf-8")
    ok, _meta = manager.commit_worker(
        state, "phase-1", "phase-1::parallel::t1", "worker commit"
    )
    assert ok is True

    status, _meta = manager.integrate_phase(state, "phase-1")
    assert status == "success"

    notes = manager.cleanup_phase(state, "phase-1")
    assert notes
    assert state["cleanup_paths"] == []
    assert state["cleanup_branches"] == []
    phase_state = state["phases"]["phase-1"]
    assert phase_state.get("integration_worktree") == ""


def test_detect_stale_artifacts_reports_orphans(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runstale1")
    state = manager.bootstrap_state()
    manager.prepare_phase(state, "phase-1")

    orphan = tmp_path / ".ralphite" / "worktrees" / "orphanrun"
    orphan.mkdir(parents=True, exist_ok=True)

    report = manager.detect_stale_artifacts(
        active_run_ids=["active-run"], max_age_hours=0
    )
    assert "stale_worktrees" in report
    assert any(item.get("run_id") == "orphanrun" for item in report["stale_worktrees"])


def test_cleanup_phase_prunes_empty_phase_and_run_directories(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runprune123")
    state = manager.bootstrap_state()

    manager.prepare_phase(state, "phase-1")
    worker = manager.prepare_worker(state, "phase-1", "phase-1::parallel::t1")
    worker_path = Path(str(worker["worktree_path"]))
    assert worker_path.exists()

    notes = manager.cleanup_phase(state, "phase-1")
    assert notes
    assert not worker_path.exists()
    assert not worker_path.parent.exists()
    assert not (tmp_path / ".ralphite" / "worktrees" / "runprune123").exists()


def test_prepare_worker_uses_compact_branch_and_worktree_names(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "12345678-1234-1234-1234-1234567890abcdef")
    state = manager.bootstrap_state()

    phase = "phase-name-with-many-segments-and-a-very-long-identifier"
    node_id = (
        "phase-1::parallel::task_backend_environment_and_operator_contract::"
        "subtask-with-a-very-long-descriptor"
    )
    worker = manager.prepare_worker(state, phase, node_id)

    worker_path = Path(str(worker["worktree_path"]))
    assert worker_path.exists()
    assert len(worker["branch"]) < len(
        f"ralphite/12345678/{phase}--{node_id.lower().replace(':', '-')}"
    )
    assert worker_path.parent.parent.name == "12345678"
    assert len(worker_path.name) < len(node_id)


def test_prepare_worker_reclaims_stale_managed_worktree_path(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runstaleworker1")
    state = manager.bootstrap_state()

    phase = "phase-1"
    node_id = "phase-1::parallel::t1"
    phase_state = manager.prepare_phase(state, phase)
    branch = manager._worker_branch_name(phase_state["phase_branch"], node_id)  # noqa: SLF001
    _git(tmp_path, "branch", branch, phase_state["phase_branch"])

    stale_path = manager._worker_worktree_path(phase, node_id)  # noqa: SLF001
    stale_path.mkdir(parents=True, exist_ok=True)
    (stale_path / "leftover.txt").write_text("stale\n", encoding="utf-8")
    managed_process_marker_path(stale_path).write_text(
        '{"pid": 999999, "backend": "codex"}', encoding="utf-8"
    )

    worker = manager.prepare_worker(state, phase, node_id)
    worker_path = Path(str(worker["worktree_path"]))
    assert worker["prepare_error"] == ""
    assert worker_path.exists()
    assert not managed_process_marker_path(worker_path).exists()
    cleanup_notes = worker.get("cleanup_notes", [])
    assert isinstance(cleanup_notes, list)
    assert any("stale" in item for item in cleanup_notes)


def test_conflict_next_commands_quote_paths_with_spaces(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runquotes123")
    commands = manager._conflict_next_commands(
        Path(r"C:\Users\alberto.mussali\Documents\My Repo\phase worktree")
    )
    assert (
        commands[0]
        == 'cd "C:\\Users\\alberto.mussali\\Documents\\My Repo\\phase worktree"'
    )


def test_cleanup_phase_preserves_retained_worker_until_explicit_discard(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runpreserve123")
    state = manager.bootstrap_state()
    worker = manager.prepare_worker(state, "phase-1", "phase-1::parallel::t1")
    worker_path = Path(str(worker["worktree_path"]))
    (worker_path / "worker.txt").write_text("worker output\n", encoding="utf-8")
    ok, commit_meta = manager.commit_worker(
        state, "phase-1", "phase-1::parallel::t1", "worker commit"
    )
    assert ok is True

    retained = manager.retain_target(
        state,
        scope="worker",
        reason="acceptance_command_failed",
        phase="phase-1",
        node_id="phase-1::parallel::t1",
        worktree_path=str(worker_path),
        branch=str(commit_meta.get("branch") or ""),
        committed=True,
    )
    assert retained.get("worktree_exists") is True

    notes = manager.cleanup_phase(state, "phase-1")
    assert any("preserved worktree" in item for item in notes)
    assert worker_path.exists()
    assert manager._branch_exists(str(commit_meta.get("branch") or ""))

    discard_notes = manager.cleanup_phase(state, "phase-1", discard_preserved=True)
    assert any("removed worktree" in item for item in discard_notes)
    assert not worker_path.exists()


def test_commit_workspace_changes_can_scope_to_specific_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runscope1")

    (tmp_path / "scoped.txt").write_text("scoped\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("other\n", encoding="utf-8")

    ok, meta = manager.commit_workspace_changes("scoped commit", paths=["scoped.txt"])
    assert ok is True, meta
    assert meta.get("mode") == "committed"

    show = _git(tmp_path, "show", "--name-only", "--pretty=format:")
    changed_files = [line.strip() for line in show.stdout.splitlines() if line.strip()]
    assert "scoped.txt" in changed_files
    assert "other.txt" not in changed_files

    status = _git(tmp_path, "status", "--porcelain")
    assert "other.txt" in status.stdout
