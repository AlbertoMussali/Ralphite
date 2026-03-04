from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower() or "item"


class GitWorktreeManager:
    def __init__(self, workspace_root: Path, run_id: str) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self.run_id = run_id
        self.git_available = self._detect_git_workspace()
        self.base_branch = self._detect_base_branch() if self.git_available else "main"

    def _detect_git_workspace(self) -> bool:
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.workspace_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:  # noqa: BLE001
            return False
        return True

    def _detect_base_branch(self) -> str:
        result = self._git(["symbolic-ref", "--short", "HEAD"], check=False)
        if result.returncode == 0:
            name = result.stdout.strip()
            if name:
                return name
        return "main"

    def _git(self, args: list[str], *, cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.workspace_root,
            check=check,
            capture_output=True,
            text=True,
        )

    def bootstrap_state(self, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        state = dict(existing or {})
        state.setdefault("enabled", self.git_available)
        state.setdefault("base_branch", self.base_branch)
        state.setdefault("phases", {})
        state.setdefault("cleanup_paths", [])
        state.setdefault("cleanup_branches", [])
        return state

    def prepare_phase(self, state: dict[str, Any], phase: str) -> dict[str, Any]:
        state = self.bootstrap_state(state)
        phases = state["phases"]
        if phase in phases:
            return phases[phase]

        phase_branch = f"ralphite/{_slug(self.run_id[:8])}/{_slug(phase)}"
        phase_state = {
            "phase_branch": phase_branch,
            "workers": {},
            "merged_workers": [],
            "integration_worktree": "",
            "integrated_to_base": False,
        }
        phases[phase] = phase_state

        if not state["enabled"]:
            return phase_state

        if self._git(["rev-parse", "--verify", phase_branch], check=False).returncode != 0:
            create = self._git(["branch", phase_branch, state["base_branch"]], check=False)
            if create.returncode != 0:
                phase_state["prepare_error"] = create.stderr.strip() or create.stdout.strip()
        state["cleanup_branches"].append(phase_branch)
        return phase_state

    def prepare_worker(self, state: dict[str, Any], phase: str, node_id: str) -> dict[str, Any]:
        phase_state = self.prepare_phase(state, phase)
        workers = phase_state["workers"]
        if node_id in workers:
            return workers[node_id]

        branch = f"{phase_state['phase_branch']}/{_slug(node_id)}"
        worktree = self.workspace_root / ".ralphite" / "worktrees" / _slug(self.run_id) / _slug(phase) / _slug(node_id)
        info = {
            "branch": branch,
            "worktree_path": str(worktree),
            "committed": False,
            "prepare_error": "",
        }
        workers[node_id] = info

        if not state.get("enabled"):
            return info

        worktree.parent.mkdir(parents=True, exist_ok=True)
        if self._git(["rev-parse", "--verify", branch], check=False).returncode != 0:
            created = self._git(["branch", branch, phase_state["phase_branch"]], check=False)
            if created.returncode != 0:
                info["prepare_error"] = created.stderr.strip() or created.stdout.strip()
                return info

        added = self._git(["worktree", "add", "--force", str(worktree), branch], check=False)
        if added.returncode != 0:
            info["prepare_error"] = added.stderr.strip() or added.stdout.strip()
            return info

        state["cleanup_paths"].append(str(worktree))
        state["cleanup_branches"].append(branch)
        return info

    def commit_worker(self, state: dict[str, Any], phase: str, node_id: str, message: str) -> tuple[bool, dict[str, Any]]:
        info = self.prepare_worker(state, phase, node_id)
        if info.get("prepare_error"):
            return False, {"reason": "worktree_prepare_failed", "error": info["prepare_error"]}

        if not state.get("enabled"):
            info["committed"] = True
            return True, {"mode": "simulated", "branch": info["branch"], "worktree": info["worktree_path"]}

        worktree = Path(info["worktree_path"])
        add = self._git(["add", "-A"], cwd=worktree, check=False)
        if add.returncode != 0:
            return False, {"reason": "git_add_failed", "error": add.stderr.strip() or add.stdout.strip()}

        commit = self._git(["commit", "--allow-empty", "-m", message], cwd=worktree, check=False)
        if commit.returncode != 0:
            return False, {"reason": "git_commit_failed", "error": commit.stderr.strip() or commit.stdout.strip()}

        info["committed"] = True
        return True, {"branch": info["branch"], "worktree": info["worktree_path"]}

    def _simulate_conflict(self, phase: str) -> bool:
        marker = (self.workspace_root / ".ralphite" / "force_merge_conflict").read_text(encoding="utf-8").strip() if (self.workspace_root / ".ralphite" / "force_merge_conflict").exists() else ""
        return marker == phase

    def integrate_phase(
        self,
        state: dict[str, Any],
        phase: str,
        *,
        recovery_mode: str = "manual",
        recovery_prompt: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        phase_state = self.prepare_phase(state, phase)
        workers = phase_state.get("workers", {})
        worker_branches = [entry["branch"] for entry in workers.values() if entry.get("committed")]

        if self._simulate_conflict(phase):
            return "recovery_required", {"reason": "simulated_conflict", "phase": phase}

        if not state.get("enabled"):
            phase_state["integrated_to_base"] = True
            return "success", {"mode": "simulated", "workers": worker_branches}

        if recovery_mode == "agent_best_effort" and recovery_prompt:
            # This marker allows the engine to show agent-assisted conflict context.
            phase_state["last_recovery_prompt"] = recovery_prompt

        integration_path = self.workspace_root / ".ralphite" / "worktrees" / _slug(self.run_id) / _slug(phase) / "integration"
        integration_path.parent.mkdir(parents=True, exist_ok=True)
        phase_state["integration_worktree"] = str(integration_path)

        add_wt = self._git(
            ["worktree", "add", "--force", str(integration_path), phase_state["phase_branch"]],
            check=False,
        )
        if add_wt.returncode != 0:
            return "failed", {"reason": "phase_worktree_add_failed", "error": add_wt.stderr.strip() or add_wt.stdout.strip()}
        state["cleanup_paths"].append(str(integration_path))

        for branch in worker_branches:
            merged = self._git(["merge", "--no-ff", "--no-edit", branch], cwd=integration_path, check=False)
            if merged.returncode != 0:
                return (
                    "recovery_required",
                    {
                        "reason": "worker_merge_conflict",
                        "phase": phase,
                        "branch": branch,
                        "error": merged.stderr.strip() or merged.stdout.strip(),
                        "worktree": str(integration_path),
                    },
                )
            phase_state["merged_workers"].append(branch)

        merged_to_base = self._git(["merge", "--no-ff", "--no-edit", phase_state["phase_branch"]], check=False)
        if merged_to_base.returncode != 0:
            return (
                "recovery_required",
                {
                    "reason": "base_merge_conflict",
                    "phase": phase,
                    "branch": phase_state["phase_branch"],
                    "error": merged_to_base.stderr.strip() or merged_to_base.stdout.strip(),
                },
            )

        phase_state["integrated_to_base"] = True
        return "success", {"phase_branch": phase_state["phase_branch"], "workers": worker_branches}

    def cleanup_phase(self, state: dict[str, Any], phase: str) -> list[str]:
        messages: list[str] = []
        phase_state = self.prepare_phase(state, phase)

        worker_paths = [entry.get("worktree_path", "") for entry in phase_state.get("workers", {}).values()]
        if phase_state.get("integration_worktree"):
            worker_paths.append(phase_state["integration_worktree"])

        if state.get("enabled"):
            for path in worker_paths:
                if not path:
                    continue
                removed = self._git(["worktree", "remove", "--force", path], check=False)
                if removed.returncode == 0:
                    messages.append(f"removed worktree {path}")
        else:
            for path in worker_paths:
                if path:
                    messages.append(f"simulated cleanup {path}")
        return messages

    def cleanup_all(self, state: dict[str, Any]) -> list[str]:
        messages: list[str] = []
        phases = list(self.bootstrap_state(state).get("phases", {}).keys())
        for phase in phases:
            messages.extend(self.cleanup_phase(state, phase))

        if state.get("enabled"):
            for branch in reversed(list(dict.fromkeys(state.get("cleanup_branches", [])))):
                deleted = self._git(["branch", "-D", branch], check=False)
                if deleted.returncode == 0:
                    messages.append(f"deleted branch {branch}")
        return messages
