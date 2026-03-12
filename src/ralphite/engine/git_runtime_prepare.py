from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ralphite.engine.git_worktree import GitWorktreeManager


class GitRuntimePrepare:
    def __init__(self, manager: "GitWorktreeManager") -> None:
        self.manager = manager

    @property
    def context(self):  # noqa: ANN201
        return self.manager.context

    def prepare_phase(self, state: dict[str, Any], phase: str) -> dict[str, Any]:
        state = self.manager._state.bootstrap_state(state)
        phases = state["phases"]
        if phase in phases:
            return phases[phase]

        phase_branch = self.manager._paths.phase_branch_name(phase)
        phase_state = {
            "phase_branch": phase_branch,
            "workers": {},
            "merged_workers": [],
            "integration_worktree": "",
            "integrated_to_base": False,
        }
        phases[phase] = phase_state

        if (
            self.manager._git(
                ["rev-parse", "--verify", phase_branch], check=False
            ).returncode
            != 0
        ):
            create = self.manager._git(
                ["branch", phase_branch, state["base_branch"]], check=False
            )
            if create.returncode != 0:
                phase_state["prepare_error"] = (
                    create.stderr.strip() or create.stdout.strip()
                )
        state["cleanup_branches"].append(phase_branch)
        return phase_state

    def prepare_worker(
        self, state: dict[str, Any], phase: str, node_id: str
    ) -> dict[str, Any]:
        phase_state = self.prepare_phase(state, phase)
        workers = phase_state["workers"]
        branch = self.manager._paths.worker_branch_name(
            phase_state["phase_branch"], node_id
        )
        worktree = self.manager._paths.worker_worktree_path(phase, node_id)
        if node_id in workers and isinstance(workers[node_id], dict):
            info = workers[node_id]
            info.setdefault("branch", branch)
            info.setdefault("worktree_path", str(worktree))
            info.setdefault("committed", False)
            info.setdefault("prepare_error", "")
            existing_path = Path(str(info.get("worktree_path") or worktree))
            if (
                existing_path.exists()
                and not str(info.get("prepare_error") or "").strip()
            ):
                return info
        else:
            info = {
                "branch": branch,
                "worktree_path": str(worktree),
                "committed": False,
                "prepare_error": "",
            }
            workers[node_id] = info

        worktree.parent.mkdir(parents=True, exist_ok=True)
        if (
            self.manager._git(["rev-parse", "--verify", branch], check=False).returncode
            != 0
        ):
            created = self.manager._git(
                ["branch", branch, phase_state["phase_branch"]], check=False
            )
            if created.returncode != 0:
                info["prepare_error"] = created.stderr.strip() or created.stdout.strip()
                return info

        if worktree.exists():
            cleaned, cleanup_messages = (
                self.manager._conflicts.cleanup_stale_managed_worktree(
                    worktree, branch=branch
                )
            )
            if cleanup_messages:
                info["cleanup_notes"] = cleanup_messages
            if not cleaned and worktree.exists():
                info["prepare_error"] = (
                    cleanup_messages[-1]
                    if cleanup_messages
                    else f"managed worktree path already exists: {worktree}"
                )
                return info

        added = self.manager._git(
            ["worktree", "add", "--force", str(worktree), branch], check=False
        )
        if added.returncode != 0:
            info["prepare_error"] = added.stderr.strip() or added.stdout.strip()
            return info

        state["cleanup_paths"].append(str(worktree))
        state["cleanup_branches"].append(branch)
        return info

    def commit_worker(
        self, state: dict[str, Any], phase: str, node_id: str, message: str
    ) -> tuple[bool, dict[str, Any]]:
        if not self.manager.git_available:
            return False, self.manager.git_required_details(self.context.workspace_root)
        info = self.prepare_worker(state, phase, node_id)
        if info.get("prepare_error"):
            return False, {
                "reason": "worktree_prepare_failed",
                "error": info["prepare_error"],
            }

        worktree = Path(info["worktree_path"])
        add = self.manager._git(["add", "-A"], cwd=worktree, check=False)
        if add.returncode != 0:
            return False, {
                "reason": "git_add_failed",
                "error": add.stderr.strip() or add.stdout.strip(),
            }

        commit = self.manager._git(
            ["commit", "--allow-empty", "-m", message], cwd=worktree, check=False
        )
        if commit.returncode != 0:
            return False, {
                "reason": "git_commit_failed",
                "error": commit.stderr.strip() or commit.stdout.strip(),
            }

        info["committed"] = True
        return True, {
            "branch": info["branch"],
            "worktree": info["worktree_path"],
            **self.manager._repo.head_commit_metadata(worktree),
        }

    def ensure_integration_worktree(
        self, state: dict[str, Any], phase: str
    ) -> tuple[bool, dict[str, Any]]:
        phase_state = self.prepare_phase(state, phase)
        integration_path = self.manager._paths.integration_worktree_path(phase)
        integration_path.parent.mkdir(parents=True, exist_ok=True)
        phase_state["integration_worktree"] = str(integration_path)
        if integration_path.exists():
            return True, {
                "phase_branch": phase_state["phase_branch"],
                "worktree": str(integration_path),
            }

        add_wt = self.manager._git(
            [
                "worktree",
                "add",
                "--force",
                str(integration_path),
                phase_state["phase_branch"],
            ],
            check=False,
        )
        if add_wt.returncode != 0:
            return False, {
                "reason": "phase_worktree_add_failed",
                "error": add_wt.stderr.strip() or add_wt.stdout.strip(),
            }
        state["cleanup_paths"].append(str(integration_path))
        return True, {
            "phase_branch": phase_state["phase_branch"],
            "worktree": str(integration_path),
        }

    def simulate_conflict(self, phase: str) -> bool:
        marker_path = self.context.workspace_root / ".ralphite" / "force_merge_conflict"
        marker = (
            marker_path.read_text(encoding="utf-8").strip()
            if marker_path.exists()
            else ""
        )
        return marker == phase

    def prepare_phase_integration(
        self, state: dict[str, Any], phase: str
    ) -> tuple[str, dict[str, Any]]:
        phase_state = self.prepare_phase(state, phase)
        workers = phase_state.get("workers", {})
        worker_branches = [
            entry["branch"] for entry in workers.values() if entry.get("committed")
        ]

        if self.simulate_conflict(phase):
            return (
                "recovery_required",
                {
                    "reason": "simulated_conflict",
                    "phase": phase,
                    "conflict_files": ["SIMULATED_CONFLICT"],
                    "next_commands": [
                        "Remove .ralphite/force_merge_conflict and retry recovery."
                    ],
                },
            )

        ok, integration_meta = self.ensure_integration_worktree(state, phase)
        if not ok:
            return "failed", integration_meta

        integration_path = Path(str(integration_meta["worktree"]))
        merged_workers = phase_state.setdefault("merged_workers", [])
        if not isinstance(merged_workers, list):
            merged_workers = []
            phase_state["merged_workers"] = merged_workers

        for branch in worker_branches:
            if branch in merged_workers:
                continue
            merged = self.manager._git(
                ["merge", "--no-ff", "--no-edit", branch],
                cwd=integration_path,
                check=False,
            )
            if merged.returncode != 0:
                merge_output = merged.stderr.strip() or merged.stdout.strip()
                resolved, resolve_meta = (
                    self.manager._conflicts.attempt_auto_resolve_merge_conflicts(
                        integration_path
                    )
                )
                if resolved:
                    merged_workers.append(branch)
                    continue
                conflict_details = (
                    self.manager._conflicts.collect_merge_conflict_details(
                        integration_path, output=merge_output
                    )
                )
                return (
                    "recovery_required",
                    {
                        "reason": "worker_merge_conflict",
                        "phase": phase,
                        "branch": branch,
                        "error": merge_output,
                        "worktree": str(integration_path),
                        **resolve_meta,
                        **conflict_details,
                        "next_commands": self.manager._conflicts.conflict_next_commands(
                            integration_path
                        ),
                    },
                )
            merged_workers.append(branch)

        return "success", {
            "phase_branch": phase_state["phase_branch"],
            "workers": worker_branches,
            "worktree": str(integration_path),
            "auto_resolved_conflicts": [],
        }

    def commit_phase_integration_changes(
        self, state: dict[str, Any], phase: str, message: str
    ) -> tuple[bool, dict[str, Any]]:
        status, details = self.prepare_phase_integration(state, phase)
        if status != "success":
            return False, details

        integration_path = Path(str(details.get("worktree") or ""))
        add = self.manager._git(["add", "-A"], cwd=integration_path, check=False)
        if add.returncode != 0:
            return False, {
                "reason": "git_add_failed",
                "error": add.stderr.strip() or add.stdout.strip(),
            }

        has_staged = self.manager._git(
            ["diff", "--cached", "--quiet"], cwd=integration_path
        )
        if has_staged.returncode == 0:
            return True, {
                "mode": "noop",
                "message": "no phase integration changes to commit",
                "worktree": str(integration_path),
                "phase_branch": str(details.get("phase_branch") or ""),
            }

        commit = self.manager._git(
            ["commit", "--allow-empty", "-m", message],
            cwd=integration_path,
            check=False,
        )
        if commit.returncode != 0:
            return False, {
                "reason": "git_commit_failed",
                "error": commit.stderr.strip() or commit.stdout.strip(),
            }

        return True, {
            "mode": "committed",
            "message": message,
            "worktree": str(integration_path),
            "phase_branch": str(details.get("phase_branch") or ""),
            **self.manager._repo.head_commit_metadata(integration_path),
        }

    def integrate_phase(
        self,
        state: dict[str, Any],
        phase: str,
        *,
        recovery_mode: str = "manual",
        recovery_prompt: str | None = None,
        ignore_overlap_paths: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        phase_state = self.prepare_phase(state, phase)
        status, prep_meta = self.prepare_phase_integration(state, phase)
        if status != "success":
            return status, prep_meta
        worker_branches = (
            prep_meta.get("workers")
            if isinstance(prep_meta.get("workers"), list)
            else []
        )

        if recovery_mode == "agent_best_effort" and recovery_prompt:
            phase_state["last_recovery_prompt"] = recovery_prompt

        precheck = self.manager._conflicts.pre_base_integration_check(
            phase_state["phase_branch"], ignore_paths=ignore_overlap_paths
        )
        if not bool(precheck.get("ok")):
            return (
                "recovery_required",
                {
                    "reason": "base_integration_blocked_by_local_changes",
                    "phase": phase,
                    "branch": phase_state["phase_branch"],
                    "worktree": str(self.context.workspace_root),
                    "overlap_files": list(precheck.get("overlap_files") or []),
                    "ignored_overlap_files": list(
                        precheck.get("ignored_overlap_files") or []
                    ),
                    "base_local_files": list(precheck.get("local_files") or []),
                    "phase_touched_files": list(precheck.get("phase_files") or []),
                    "conflict_files": [],
                    "current_run_conflict_files": [],
                    "next_commands": [
                        "git status --short",
                        "Commit, stash, or reconcile the overlapping files before resuming recovery.",
                        "Run `uv run ralphite recover --workspace . --preflight-only --output table` to inspect the blocked run.",
                    ],
                },
            )

        merged_to_base = self.manager._git(
            ["merge", "--no-ff", "--no-edit", phase_state["phase_branch"]], check=False
        )
        if merged_to_base.returncode != 0:
            merge_output = (
                merged_to_base.stderr.strip() or merged_to_base.stdout.strip()
            )
            resolved, resolve_meta = (
                self.manager._conflicts.attempt_auto_resolve_merge_conflicts(
                    self.context.workspace_root
                )
            )
            if resolved:
                phase_state["integrated_to_base"] = True
                return "success", {
                    "phase_branch": phase_state["phase_branch"],
                    "workers": worker_branches,
                    **resolve_meta,
                }
            conflict_details = self.manager._conflicts.collect_merge_conflict_details(
                self.context.workspace_root, output=merge_output
            )
            return (
                "recovery_required",
                {
                    "reason": "base_merge_conflict",
                    "phase": phase,
                    "branch": phase_state["phase_branch"],
                    "error": merge_output,
                    "worktree": str(self.context.workspace_root),
                    **resolve_meta,
                    **conflict_details,
                    "next_commands": self.manager._conflicts.conflict_next_commands(
                        self.context.workspace_root
                    ),
                },
            )

        phase_state["integrated_to_base"] = True
        return "success", {
            "phase_branch": phase_state["phase_branch"],
            "workers": worker_branches,
        }
