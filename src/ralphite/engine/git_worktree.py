from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from ralphite.engine.git_runtime_cleanup import GitRuntimeCleanup
from ralphite.engine.git_runtime_conflicts import GitRuntimeConflicts
from ralphite.engine.git_runtime_context import GitRuntimeContext
from ralphite.engine.git_runtime_paths import GitRuntimePaths, _quote_shell_path
from ralphite.engine.git_runtime_prepare import GitRuntimePrepare
from ralphite.engine.git_runtime_repo import GitRuntimeRepo
from ralphite.engine.git_runtime_state import GitRuntimeState


def git_required_details(workspace_root: Path) -> dict[str, Any]:
    root = workspace_root.expanduser().resolve()
    return {
        "reason": "git_required",
        "workspace_root": str(root),
        "detail": "workspace must be inside a git worktree for Ralphite execution",
    }


class GitRequiredError(RuntimeError):
    def __init__(self, workspace_root: Path) -> None:
        self.details = git_required_details(workspace_root)
        super().__init__(str(self.details["detail"]))


class GitWorktreeManager:
    def __init__(self, workspace_root: Path, run_id: str) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self.run_id = run_id
        self.context = GitRuntimeContext(
            workspace_root=self.workspace_root, run_id=run_id, base_branch="main"
        )

        self._paths = GitRuntimePaths(self)
        self._repo = GitRuntimeRepo(self)
        self._conflicts = GitRuntimeConflicts(self)
        self._state = GitRuntimeState(self)
        self._prepare = GitRuntimePrepare(self)
        self._cleanup = GitRuntimeCleanup(self)

        self.git_available = self._detect_git_workspace()
        self.base_branch = self._detect_base_branch() if self.git_available else "main"
        self.context.base_branch = self.base_branch

    def _detect_git_workspace(self) -> bool:
        return self._repo.detect_git_workspace()

    def _detect_base_branch(self) -> str:
        return self._repo.detect_base_branch()

    def repository_status(self) -> dict[str, Any]:
        return self._repo.repository_status()

    def execution_status(self) -> dict[str, Any]:
        return self._repo.execution_status()

    def runtime_status(self) -> dict[str, Any]:
        return self.execution_status()

    def _ensure_git_available(self) -> None:
        if not self.git_available:
            raise GitRequiredError(self.workspace_root)

    def _git(
        self, args: list[str], *, cwd: Path | None = None, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.workspace_root,
            check=check,
            capture_output=True,
            text=True,
        )

    def _worktrees_root(self) -> Path:
        return self._paths.worktrees_root()

    def _run_key(self) -> str:
        return self._paths.run_key()

    def _run_key_for(self, run_id: str | None = None) -> str:
        return self._paths.run_key_for(run_id)

    def _phase_key(self, phase: str) -> str:
        return self._paths.phase_key(phase)

    def _node_key(self, node_id: str) -> str:
        return self._paths.node_key(node_id)

    def _phase_branch_name(self, phase: str) -> str:
        return self._paths.phase_branch_name(phase)

    def _worker_branch_name(self, phase_branch: str, node_id: str) -> str:
        return self._paths.worker_branch_name(phase_branch, node_id)

    def _phase_worktrees_root(self, phase: str) -> Path:
        return self._paths.phase_worktrees_root(phase)

    def _worker_worktree_path(self, phase: str, node_id: str) -> Path:
        return self._paths.worker_worktree_path(phase, node_id)

    def _integration_worktree_path(self, phase: str) -> Path:
        return self._paths.integration_worktree_path(phase)

    def _prune_empty_worktree_ancestors(self, path: Path) -> list[str]:
        return self._paths.prune_empty_worktree_ancestors(path)

    def _cleanup_stale_managed_worktree(
        self, path: Path, *, branch: str = ""
    ) -> tuple[bool, list[str]]:
        return self._conflicts.cleanup_stale_managed_worktree(path, branch=branch)

    def _head_commit_metadata(self, cwd: Path) -> dict[str, Any]:
        return self._repo.head_commit_metadata(cwd)

    def _workspace_local_changes(self, cwd: Path | None = None) -> list[str]:
        return self._repo.workspace_local_changes(cwd)

    def _phase_touched_files(self, branch: str) -> list[str]:
        return self._repo.phase_touched_files(branch)

    def _normalize_rel_path(self, raw_path: str | Path) -> str:
        return self._repo.normalize_rel_path(raw_path)

    def _normalize_rel_paths(self, raw_paths: list[str] | tuple[str, ...]) -> set[str]:
        return self._repo.normalize_rel_paths(raw_paths)

    def _classify_delete_failure(self, path: Path, detail: str) -> str:
        return self._conflicts.classify_delete_failure(path, detail)

    def _delete_tree_with_retry(
        self, path: Path, *, branch: str = "", stale: bool = False
    ) -> tuple[bool, list[str]]:
        return self._conflicts.delete_tree_with_retry(path, branch=branch, stale=stale)

    def _parse_merge_blocked_files(self, output: str) -> list[str]:
        return self._conflicts.parse_merge_blocked_files(output)

    def _tracked_unmerged_files(self, worktree: Path) -> list[str]:
        return self._conflicts.tracked_unmerged_files(worktree)

    def _collect_merge_conflict_details(
        self, worktree: Path, *, output: str = ""
    ) -> dict[str, Any]:
        return self._conflicts.collect_merge_conflict_details(worktree, output=output)

    def _remove_managed_worktree_path(
        self, path: Path, *, branch: str = "", stale: bool = False
    ) -> tuple[bool, list[str]]:
        return self._conflicts.remove_managed_worktree_path(
            path, branch=branch, stale=stale
        )

    def _merge_conflict_blocks(
        self, text: str
    ) -> tuple[list[tuple[str, list[str], list[str]]], bool]:
        return self._conflicts.merge_conflict_blocks(text)

    def _merge_unique_lines(self, left: list[str], right: list[str]) -> list[str]:
        return self._conflicts.merge_unique_lines(left, right)

    def _conflict_resolver_kind(
        self, path: Path, ours: list[str], theirs: list[str]
    ) -> str | None:
        return self._conflicts.conflict_resolver_kind(path, ours, theirs)

    def _auto_resolve_conflict_file(self, path: Path) -> tuple[bool, dict[str, Any]]:
        return self._conflicts.auto_resolve_conflict_file(path)

    def _attempt_auto_resolve_merge_conflicts(
        self, worktree: Path
    ) -> tuple[bool, dict[str, Any]]:
        return self._conflicts.attempt_auto_resolve_merge_conflicts(worktree)

    def pre_base_integration_check(
        self, phase_branch: str, *, ignore_paths: list[str] | None = None
    ) -> dict[str, Any]:
        return self._conflicts.pre_base_integration_check(
            phase_branch, ignore_paths=ignore_paths
        )

    def _conflict_next_commands(self, worktree: Path) -> list[str]:
        return self._conflicts.conflict_next_commands(worktree)

    def _collect_conflict_files(self, worktree: Path) -> list[str]:
        return self._conflicts.collect_conflict_files(worktree)

    def bootstrap_state(self, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._state.bootstrap_state(existing)

    def _preserved_paths(self, state: dict[str, Any]) -> set[str]:
        return self._state.preserved_paths(state)

    def _preserved_branches(self, state: dict[str, Any]) -> set[str]:
        return self._state.preserved_branches(state)

    def _branch_exists(self, branch: str) -> bool:
        return self._repo.branch_exists(branch)

    def inspect_managed_target(
        self,
        *,
        worktree_path: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        return self._state.inspect_managed_target(
            worktree_path=worktree_path, branch=branch
        )

    def retain_target(
        self,
        state: dict[str, Any],
        *,
        scope: str,
        reason: str,
        phase: str = "",
        node_id: str = "",
        worktree_path: str | None = None,
        branch: str | None = None,
        failure_title: str = "",
        failed_command: str = "",
        error: str = "",
        committed: bool | None = None,
        stdout: str = "",
        stderr: str = "",
        backend_payload: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._state.retain_target(
            state,
            scope=scope,
            reason=reason,
            phase=phase,
            node_id=node_id,
            worktree_path=worktree_path,
            branch=branch,
            failure_title=failure_title,
            failed_command=failed_command,
            error=error,
            committed=committed,
            stdout=stdout,
            stderr=stderr,
            backend_payload=backend_payload,
            diagnostics=diagnostics,
        )

    def retain_all_managed_work(
        self, state: dict[str, Any], *, reason: str, failure_title: str = ""
    ) -> list[dict[str, Any]]:
        return self._state.retain_all_managed_work(
            state, reason=reason, failure_title=failure_title
        )

    def reconcile_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._state.reconcile_state(state)

    def phase_cleanup_allowed(self, state: dict[str, Any], phase: str) -> bool:
        return self._state.phase_cleanup_allowed(state, phase)

    def prepare_phase(self, state: dict[str, Any], phase: str) -> dict[str, Any]:
        return self._prepare.prepare_phase(state, phase)

    def prepare_worker(
        self, state: dict[str, Any], phase: str, node_id: str
    ) -> dict[str, Any]:
        return self._prepare.prepare_worker(state, phase, node_id)

    def commit_worker(
        self, state: dict[str, Any], phase: str, node_id: str, message: str
    ) -> tuple[bool, dict[str, Any]]:
        return self._prepare.commit_worker(state, phase, node_id, message)

    def _ensure_integration_worktree(
        self, state: dict[str, Any], phase: str
    ) -> tuple[bool, dict[str, Any]]:
        return self._prepare.ensure_integration_worktree(state, phase)

    def prepare_phase_integration(
        self, state: dict[str, Any], phase: str
    ) -> tuple[str, dict[str, Any]]:
        return self._prepare.prepare_phase_integration(state, phase)

    def commit_phase_integration_changes(
        self, state: dict[str, Any], phase: str, message: str
    ) -> tuple[bool, dict[str, Any]]:
        return self._prepare.commit_phase_integration_changes(state, phase, message)

    def _simulate_conflict(self, phase: str) -> bool:
        return self._prepare.simulate_conflict(phase)

    def integrate_phase(
        self,
        state: dict[str, Any],
        phase: str,
        *,
        recovery_mode: str = "manual",
        recovery_prompt: str | None = None,
        ignore_overlap_paths: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return self._prepare.integrate_phase(
            state,
            phase,
            recovery_mode=recovery_mode,
            recovery_prompt=recovery_prompt,
            ignore_overlap_paths=ignore_overlap_paths,
        )

    def list_managed_branches(self, state: dict[str, Any]) -> list[str]:
        return self._state.list_managed_branches(state)

    def list_managed_worktrees(self, state: dict[str, Any]) -> list[str]:
        return self._state.list_managed_worktrees(state)

    def detect_stale_artifacts(
        self,
        active_run_ids: list[str],
        max_age_hours: int = 24,
    ) -> dict[str, list[dict[str, Any]]]:
        return self._conflicts.detect_stale_artifacts(
            active_run_ids=active_run_ids, max_age_hours=max_age_hours
        )

    def managed_artifact_inventory(self, run_id: str | None = None) -> dict[str, Any]:
        return self._state.managed_artifact_inventory(run_id)

    def cleanup_orphaned_run_artifacts(self, run_id: str | None = None) -> list[str]:
        return self._cleanup.cleanup_orphaned_run_artifacts(run_id)

    def cleanup_phase(
        self, state: dict[str, Any], phase: str, *, discard_preserved: bool = False
    ) -> list[str]:
        return self._cleanup.cleanup_phase(
            state, phase, discard_preserved=discard_preserved
        )

    def cleanup_all(
        self, state: dict[str, Any], *, discard_preserved: bool = False
    ) -> list[str]:
        return self._cleanup.cleanup_all(state, discard_preserved=discard_preserved)

    def commit_workspace_changes(
        self, message: str, paths: list[str] | None = None
    ) -> tuple[bool, dict[str, Any]]:
        return self._cleanup.commit_workspace_changes(message, paths=paths)

    def _quote_shell_path(self, path: Path | str) -> str:
        return _quote_shell_path(path)
