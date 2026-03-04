from __future__ import annotations

from pathlib import Path
import subprocess

from ralphite_engine.git_worktree import GitWorktreeManager


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    _git(path, "init")
    subprocess.run(["git", "branch", "-m", "main"], cwd=path, check=False, capture_output=True, text=True)
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
    ok, commit_meta = manager.commit_worker(state, "phase-1", "phase-1::parallel::t1", "worker commit")
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
    assert cleanup_second


def test_git_worktree_conflict_fail_closed_reports_details(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runconf123")
    state = manager.bootstrap_state()

    manager.prepare_phase(state, "phase-1")
    worker = manager.prepare_worker(state, "phase-1", "phase-1::parallel::t1")
    worker_path = Path(str(worker["worktree_path"]))
    (worker_path / "shared.txt").write_text("worker-change\n", encoding="utf-8")
    ok, _meta = manager.commit_worker(state, "phase-1", "phase-1::parallel::t1", "worker changes shared file")
    assert ok is True

    (tmp_path / "shared.txt").write_text("base-change\n", encoding="utf-8")
    _git(tmp_path, "add", "shared.txt")
    _git(tmp_path, "commit", "-m", "base changes shared file")

    status, details = manager.integrate_phase(state, "phase-1")
    assert status == "recovery_required"
    assert details.get("reason") in {"base_merge_conflict", "worker_merge_conflict"}
    assert isinstance(details.get("conflict_files"), list)
    assert details.get("next_commands")


def test_detect_stale_artifacts_reports_orphans(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    manager = GitWorktreeManager(tmp_path, "runstale1")
    state = manager.bootstrap_state()
    manager.prepare_phase(state, "phase-1")

    orphan = tmp_path / ".ralphite" / "worktrees" / "orphanrun"
    orphan.mkdir(parents=True, exist_ok=True)

    report = manager.detect_stale_artifacts(active_run_ids=["active-run"], max_age_hours=0)
    assert "stale_worktrees" in report
    assert any(item.get("run_id") == "orphanrun" for item in report["stale_worktrees"])
