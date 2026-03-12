from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ralphite.engine.git_worktree import GitWorktreeManager


class GitRuntimeCleanup:
    def __init__(self, manager: "GitWorktreeManager") -> None:
        self.manager = manager

    def cleanup_orphaned_run_artifacts(self, run_id: str | None = None) -> list[str]:
        self.manager._ensure_git_available()
        inventory = self.manager._state.managed_artifact_inventory(run_id)
        messages: list[str] = []

        for item in inventory.get("worktrees", []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            path_obj = Path(path)
            if not path_obj.exists():
                messages.append(f"missing worktree metadata will be pruned {path}")
                continue
            _removed, remove_messages = (
                self.manager._conflicts.remove_managed_worktree_path(
                    path_obj, branch=str(item.get("branch") or ""), stale=False
                )
            )
            messages.extend(remove_messages)

        pruned = self.manager._git(["worktree", "prune"], check=False)
        if pruned.returncode == 0 and (pruned.stdout.strip() or pruned.stderr.strip()):
            messages.append(pruned.stderr.strip() or pruned.stdout.strip())

        branch_names = [
            str(item.get("branch") or "")
            for item in inventory.get("branches", [])
            if isinstance(item, dict)
        ]
        worktree_branch_names = [
            str(item.get("branch") or "")
            for item in inventory.get("worktrees", [])
            if isinstance(item, dict)
        ]
        for branch in reversed(
            list(
                dict.fromkeys(
                    [item for item in branch_names + worktree_branch_names if item]
                )
            )
        ):
            exists = self.manager._git(["rev-parse", "--verify", branch], check=False)
            if exists.returncode != 0:
                messages.append(f"branch already removed {branch}")
                continue
            deleted = self.manager._git(["branch", "-D", branch], check=False)
            if deleted.returncode == 0:
                messages.append(f"deleted branch {branch}")
            else:
                messages.append(
                    f"branch delete skipped {branch}: {deleted.stderr.strip() or deleted.stdout.strip()}"
                )

        self.manager._git(["worktree", "prune"], check=False)
        return messages

    def cleanup_phase(
        self, state: dict[str, Any], phase: str, *, discard_preserved: bool = False
    ) -> list[str]:
        self.manager._ensure_git_available()
        messages: list[str] = []
        phase_state = self.manager._prepare.prepare_phase(state, phase)
        removed_paths: list[str] = []
        removed_branches: list[str] = []
        preserved_paths = (
            set() if discard_preserved else self.manager._state.preserved_paths(state)
        )
        preserved_branches = (
            set()
            if discard_preserved
            else self.manager._state.preserved_branches(state)
        )

        worker_paths = [
            entry.get("worktree_path", "")
            for entry in phase_state.get("workers", {}).values()
        ]
        if phase_state.get("integration_worktree"):
            worker_paths.append(phase_state["integration_worktree"])

        for path in sorted(dict.fromkeys([item for item in worker_paths if item])):
            path_obj = Path(path)
            if path in preserved_paths:
                messages.append(f"preserved worktree {path}")
                continue
            if not path_obj.exists():
                messages.append(f"worktree already removed {path}")
                messages.extend(
                    self.manager._paths.prune_empty_worktree_ancestors(path_obj.parent)
                )
                continue
            branch_hint = ""
            if path == str(phase_state.get("integration_worktree") or ""):
                branch_hint = str(phase_state.get("phase_branch") or "")
            else:
                for worker in phase_state.get("workers", {}).values():
                    if (
                        isinstance(worker, dict)
                        and str(worker.get("worktree_path") or "") == path
                    ):
                        branch_hint = str(worker.get("branch") or "")
                        break
            removed, remove_messages = (
                self.manager._conflicts.remove_managed_worktree_path(
                    path_obj, branch=branch_hint, stale=False
                )
            )
            messages.extend(remove_messages)
            if removed:
                removed_paths.append(path)

        phase_branches = [phase_state.get("phase_branch", "")]
        phase_branches.extend(
            str(entry.get("branch", ""))
            for entry in phase_state.get("workers", {}).values()
            if isinstance(entry, dict)
        )
        for branch in reversed(
            list(dict.fromkeys([item for item in phase_branches if item]))
        ):
            if branch in preserved_branches:
                messages.append(f"preserved branch {branch}")
                continue
            exists = self.manager._git(["rev-parse", "--verify", branch], check=False)
            if exists.returncode != 0:
                messages.append(f"branch already removed {branch}")
                continue
            deleted = self.manager._git(["branch", "-D", branch], check=False)
            if deleted.returncode == 0:
                messages.append(f"deleted branch {branch}")
                removed_branches.append(branch)
            else:
                messages.append(
                    f"branch delete skipped {branch}: {deleted.stderr.strip() or deleted.stdout.strip()}"
                )

        state["cleanup_paths"] = [
            item for item in state.get("cleanup_paths", []) if item not in removed_paths
        ]
        state["cleanup_branches"] = [
            item
            for item in state.get("cleanup_branches", [])
            if item not in removed_branches
        ]
        for worker in phase_state.get("workers", {}).values():
            if not isinstance(worker, dict):
                continue
            if str(worker.get("worktree_path") or "") in removed_paths:
                worker["worktree_path"] = ""
            if str(worker.get("branch") or "") in removed_branches:
                worker["branch"] = ""
        if str(phase_state.get("integration_worktree") or "") in removed_paths:
            phase_state["integration_worktree"] = ""
        if str(phase_state.get("phase_branch") or "") in removed_branches:
            phase_state["phase_branch"] = ""
        return messages

    def cleanup_all(
        self, state: dict[str, Any], *, discard_preserved: bool = False
    ) -> list[str]:
        self.manager._ensure_git_available()
        messages: list[str] = []
        phases = list(
            self.manager._state.bootstrap_state(state).get("phases", {}).keys()
        )
        for phase in phases:
            messages.extend(
                self.cleanup_phase(
                    state, phase, discard_preserved=bool(discard_preserved)
                )
            )
        return messages

    def commit_workspace_changes(
        self, message: str, paths: list[str] | None = None
    ) -> tuple[bool, dict[str, Any]]:
        if not self.manager.git_available:
            return False, self.manager.git_required_details(
                self.manager.context.workspace_root
            )

        if paths:
            staged_paths: list[str] = []
            for raw_path in paths:
                path_obj = Path(raw_path).expanduser()
                if path_obj.is_absolute():
                    try:
                        path_obj = path_obj.resolve().relative_to(
                            self.manager.context.workspace_root
                        )
                    except Exception:
                        continue
                add_one = self.manager._git(["add", "--", str(path_obj)], check=False)
                if add_one.returncode != 0:
                    return False, {
                        "reason": "git_add_failed",
                        "path": str(path_obj),
                        "error": add_one.stderr.strip() or add_one.stdout.strip(),
                    }
                staged_paths.append(str(path_obj))

            has_staged = self.manager._git(["diff", "--cached", "--quiet"], check=False)
            if has_staged.returncode == 0:
                return True, {
                    "mode": "noop",
                    "message": "no staged changes to commit",
                    "paths": staged_paths,
                }
        else:
            status = self.manager._git(["status", "--porcelain"], check=False)
            if status.returncode != 0:
                return False, {
                    "reason": "git_status_failed",
                    "error": status.stderr.strip() or status.stdout.strip(),
                }
            if not status.stdout.strip():
                return True, {
                    "mode": "noop",
                    "message": "no workspace changes to commit",
                }

            add = self.manager._git(["add", "-A"], check=False)
            if add.returncode != 0:
                return False, {
                    "reason": "git_add_failed",
                    "error": add.stderr.strip() or add.stdout.strip(),
                }

        commit = self.manager._git(["commit", "-m", message], check=False)
        if commit.returncode != 0:
            return False, {
                "reason": "git_commit_failed",
                "error": commit.stderr.strip() or commit.stdout.strip(),
            }

        return True, {
            "mode": "committed",
            "message": message,
            "paths": list(paths or []),
            **self.manager._repo.head_commit_metadata(
                self.manager.context.workspace_root
            ),
        }
