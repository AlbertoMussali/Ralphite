from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ralphite.engine.git_worktree import GitWorktreeManager


class GitRuntimeState:
    def __init__(self, manager: "GitWorktreeManager") -> None:
        self.manager = manager

    def bootstrap_state(self, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        self.manager._ensure_git_available()
        state = dict(existing or {})
        state.setdefault("base_branch", self.manager.base_branch)
        state.setdefault("phases", {})
        state.setdefault("cleanup_paths", [])
        state.setdefault("cleanup_branches", [])
        state.setdefault("preserved_paths", [])
        state.setdefault("preserved_branches", [])
        state.setdefault("retained_work", [])
        return state

    def preserved_paths(self, state: dict[str, Any]) -> set[str]:
        return {
            str(item)
            for item in self.bootstrap_state(state).get("preserved_paths", [])
            if str(item).strip()
        }

    def preserved_branches(self, state: dict[str, Any]) -> set[str]:
        return {
            str(item)
            for item in self.bootstrap_state(state).get("preserved_branches", [])
            if str(item).strip()
        }

    def inspect_managed_target(
        self,
        *,
        worktree_path: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        info: dict[str, Any] = {
            "worktree_path": str(worktree_path or ""),
            "branch": str(branch or ""),
            "worktree_exists": False,
            "branch_exists": False,
            "status_porcelain": "",
            "diff": "",
            "cached_diff": "",
            "commit": "",
            "changed_files": [],
        }
        branch_name = str(branch or "").strip()
        worktree = (
            Path(str(worktree_path)).expanduser().resolve()
            if str(worktree_path or "").strip()
            else None
        )
        if worktree is not None and worktree.exists():
            info["worktree_exists"] = True
            status = self.manager._git(
                ["status", "--porcelain", "--untracked-files=all"],
                cwd=worktree,
                check=False,
            )
            if status.returncode == 0:
                info["status_porcelain"] = status.stdout
            diff = self.manager._git(["diff", "--binary"], cwd=worktree, check=False)
            if diff.returncode == 0:
                info["diff"] = diff.stdout
            cached = self.manager._git(
                ["diff", "--cached", "--binary"], cwd=worktree, check=False
            )
            if cached.returncode == 0:
                info["cached_diff"] = cached.stdout
            info.update(self.manager._repo.head_commit_metadata(worktree))
        if branch_name:
            info["branch_exists"] = self.manager._repo.branch_exists(branch_name)
            if not info.get("commit") and info["branch_exists"]:
                commit = self.manager._git(["rev-parse", branch_name], check=False)
                if commit.returncode == 0:
                    info["commit"] = commit.stdout.strip()
        return info

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
        state = self.bootstrap_state(state)
        path_text = str(worktree_path or "").strip()
        branch_text = str(branch or "").strip()
        if path_text and path_text not in state["preserved_paths"]:
            state["preserved_paths"].append(path_text)
        if branch_text and branch_text not in state["preserved_branches"]:
            state["preserved_branches"].append(branch_text)
        snapshot = self.inspect_managed_target(
            worktree_path=path_text or None,
            branch=branch_text or None,
        )
        salvage_class = "orphan_managed_artifact"
        if snapshot.get("commit"):
            salvage_class = "committed_unmerged"
        elif str(snapshot.get("status_porcelain") or "").strip():
            salvage_class = "dirty_uncommitted"
        entry = {
            "scope": scope,
            "reason": reason,
            "phase": phase,
            "node_id": node_id,
            "failure_title": failure_title,
            "failed_command": failed_command,
            "error": error,
            "committed": committed,
            "stdout": stdout,
            "stderr": stderr,
            "backend_payload": backend_payload or {},
            "diagnostics": diagnostics or {},
            "salvage_class": salvage_class,
            **snapshot,
        }
        retained = state.setdefault("retained_work", [])
        key = (scope, phase, node_id, path_text, branch_text, reason)
        for index, existing in enumerate(retained):
            if not isinstance(existing, dict):
                continue
            existing_key = (
                str(existing.get("scope") or ""),
                str(existing.get("phase") or ""),
                str(existing.get("node_id") or ""),
                str(existing.get("worktree_path") or ""),
                str(existing.get("branch") or ""),
                str(existing.get("reason") or ""),
            )
            if existing_key == key:
                retained[index] = entry
                return entry
        retained.append(entry)
        return entry

    def retain_all_managed_work(
        self, state: dict[str, Any], *, reason: str, failure_title: str = ""
    ) -> list[dict[str, Any]]:
        state = self.bootstrap_state(state)
        retained: list[dict[str, Any]] = []
        for phase, phase_state in state.get("phases", {}).items():
            if not isinstance(phase_state, dict):
                continue
            phase_branch = str(phase_state.get("phase_branch") or "")
            integration_worktree = str(phase_state.get("integration_worktree") or "")
            if phase_branch or integration_worktree:
                retained.append(
                    self.retain_target(
                        state,
                        scope="phase",
                        reason=reason,
                        phase=str(phase),
                        worktree_path=integration_worktree or None,
                        branch=phase_branch or None,
                        failure_title=failure_title,
                    )
                )
            workers = phase_state.get("workers", {})
            if not isinstance(workers, dict):
                continue
            for node_id, worker in workers.items():
                if not isinstance(worker, dict):
                    continue
                retained.append(
                    self.retain_target(
                        state,
                        scope="worker",
                        reason=reason,
                        phase=str(phase),
                        node_id=str(node_id),
                        worktree_path=str(worker.get("worktree_path") or "") or None,
                        branch=str(worker.get("branch") or "") or None,
                        failure_title=failure_title,
                        committed=bool(worker.get("committed")),
                    )
                )
        return retained

    def reconcile_state(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self.bootstrap_state(state)
        summary = {"preserved_paths": [], "preserved_branches": [], "retained_work": []}
        for entry in state.get("retained_work", []):
            if not isinstance(entry, dict):
                continue
            refreshed = self.inspect_managed_target(
                worktree_path=str(entry.get("worktree_path") or "") or None,
                branch=str(entry.get("branch") or "") or None,
            )
            entry.update(refreshed)
            summary["retained_work"].append(
                {
                    "scope": str(entry.get("scope") or ""),
                    "phase": str(entry.get("phase") or ""),
                    "node_id": str(entry.get("node_id") or ""),
                    "worktree_exists": bool(entry.get("worktree_exists")),
                    "branch_exists": bool(entry.get("branch_exists")),
                    "commit": str(entry.get("commit") or ""),
                }
            )
        summary["preserved_paths"] = sorted(self.preserved_paths(state))
        summary["preserved_branches"] = sorted(self.preserved_branches(state))
        return summary

    def phase_cleanup_allowed(self, state: dict[str, Any], phase: str) -> bool:
        phase_state = self.manager._prepare.prepare_phase(state, phase)
        if not bool(phase_state.get("integrated_to_base")):
            return False
        preserved_paths = self.preserved_paths(state)
        preserved_branches = self.preserved_branches(state)
        paths = [
            str(phase_state.get("integration_worktree") or ""),
            *[
                str(entry.get("worktree_path") or "")
                for entry in phase_state.get("workers", {}).values()
                if isinstance(entry, dict)
            ],
        ]
        branches = [
            str(phase_state.get("phase_branch") or ""),
            *[
                str(entry.get("branch") or "")
                for entry in phase_state.get("workers", {}).values()
                if isinstance(entry, dict)
            ],
        ]
        return not any(path and path in preserved_paths for path in paths) and not any(
            branch and branch in preserved_branches for branch in branches
        )

    def list_managed_branches(self, state: dict[str, Any]) -> list[str]:
        state = self.bootstrap_state(state)
        branches: list[str] = []
        branches.extend(str(item) for item in state.get("cleanup_branches", []) if item)
        for phase_state in state.get("phases", {}).values():
            if isinstance(phase_state, dict):
                branch = phase_state.get("phase_branch")
                if isinstance(branch, str) and branch:
                    branches.append(branch)
                workers = phase_state.get("workers", {})
                if isinstance(workers, dict):
                    for worker in workers.values():
                        if isinstance(worker, dict):
                            b = worker.get("branch")
                            if isinstance(b, str) and b:
                                branches.append(b)
        return sorted(dict.fromkeys(branches))

    def list_managed_worktrees(self, state: dict[str, Any]) -> list[str]:
        state = self.bootstrap_state(state)
        paths: list[str] = []
        for phase_state in state.get("phases", {}).values():
            if not isinstance(phase_state, dict):
                continue
            integration = phase_state.get("integration_worktree")
            if isinstance(integration, str) and integration:
                paths.append(integration)
            workers = phase_state.get("workers", {})
            if isinstance(workers, dict):
                for worker in workers.values():
                    if isinstance(worker, dict):
                        wpath = worker.get("worktree_path")
                        if isinstance(wpath, str) and wpath:
                            paths.append(wpath)
        paths.extend(str(item) for item in state.get("cleanup_paths", []) if item)
        return sorted(dict.fromkeys(paths))

    def managed_artifact_inventory(self, run_id: str | None = None) -> dict[str, Any]:
        self.manager._ensure_git_available()
        run_ref = str(run_id or self.manager.run_id)
        run_key = self.manager._paths.run_key_for(run_ref)
        branch_prefix = f"ralphite/{run_key}"

        branches: list[dict[str, Any]] = []
        result = self.manager._git(
            ["branch", "--list", f"{branch_prefix}*"], check=False
        )
        if result.returncode == 0:
            for raw in result.stdout.splitlines():
                branch = raw.strip().lstrip("* ").strip()
                if not branch:
                    continue
                commit = self.manager._git(["rev-parse", branch], check=False)
                branches.append(
                    {
                        "branch": branch,
                        "commit": commit.stdout.strip()
                        if commit.returncode == 0
                        else "",
                        "exists": True,
                    }
                )

        worktrees: list[dict[str, Any]] = []
        listing = self.manager._git(["worktree", "list", "--porcelain"], check=False)
        if listing.returncode == 0:
            current: dict[str, str] = {}
            for raw in listing.stdout.splitlines() + [""]:
                line = raw.strip()
                if not line:
                    path = current.get("worktree", "")
                    branch_ref = current.get("branch", "")
                    branch = (
                        branch_ref.removeprefix("refs/heads/")
                        if branch_ref.startswith("refs/heads/")
                        else branch_ref
                    )
                    normalized_path = path.replace("\\", "/").lower()
                    path_matches = (
                        f"/.ralphite/worktrees/{run_key.lower()}/" in normalized_path
                    )
                    if path and (path_matches or branch.startswith(branch_prefix)):
                        worktrees.append(
                            {
                                "path": path,
                                "branch": branch,
                                "commit": current.get("HEAD", ""),
                                "exists": Path(path).exists(),
                                "prunable": current.get("prunable", ""),
                            }
                        )
                    current = {}
                    continue
                key, _, value = line.partition(" ")
                current[key] = value

        return {
            "run_id": run_ref,
            "run_key": run_key,
            "branches": branches,
            "worktrees": worktrees,
        }
