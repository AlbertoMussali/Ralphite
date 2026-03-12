from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import shutil
from pathlib import Path
import re
import subprocess
from typing import Any

from ralphite.engine.process_guard import cleanup_managed_process_marker


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower() or "item"


def _compact_slug(
    value: str,
    *,
    prefix_len: int,
    hash_len: int = 8,
    fallback: str = "item",
) -> str:
    slug = _slug(value)
    if len(slug) <= prefix_len:
        return slug or fallback
    prefix = slug[:prefix_len].rstrip("-.") or fallback
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:hash_len]
    return f"{prefix}-{digest}"


def _quote_shell_path(path: Path | str) -> str:
    text = str(path)
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


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

    def repository_status(self) -> dict[str, Any]:
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.workspace_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return {
                "ok": False,
                "reason": "git_required",
                "workspace_root": str(self.workspace_root),
                "detail": "workspace must be inside a git worktree for Ralphite execution",
                "remediation": "git init -b main",
            }

        result = self._git(["rev-parse", "HEAD"], check=False)
        if result.returncode != 0:
            return {
                "ok": False,
                "reason": "git_required",
                "workspace_root": str(self.workspace_root),
                "detail": "git repo has no initial commit",
                "remediation": 'git add -A && git commit -m "initial workspace state"',
            }

        status = self._git(["status", "--porcelain"], check=False)
        return {
            "ok": True,
            "workspace_root": str(self.workspace_root),
            "base_branch": self.base_branch,
            "dirty": status.returncode == 0 and bool(status.stdout.strip()),
            "detail": f"git worktree detected (base branch: {self.base_branch})",
        }

    def execution_status(self) -> dict[str, Any]:
        repo = self.repository_status()
        if not bool(repo.get("ok")):
            return repo
        if bool(repo.get("dirty")):
            return {
                "ok": False,
                "reason": "git_required",
                "workspace_root": str(self.workspace_root),
                "base_branch": self.base_branch,
                "dirty": True,
                "detail": "worktree is dirty in a blocking way",
                "remediation": 'git add -A && git commit -m "save state"',
            }
        return {**repo, "dirty": False}

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
        return self.workspace_root / ".ralphite" / "worktrees"

    def _run_key(self) -> str:
        return _compact_slug(self.run_id[:8] or self.run_id, prefix_len=12)

    def _run_key_for(self, run_id: str | None = None) -> str:
        candidate = str(run_id or self.run_id)
        return _compact_slug(candidate[:8] or candidate, prefix_len=12)

    def _phase_key(self, phase: str) -> str:
        return _compact_slug(phase, prefix_len=20)

    def _node_key(self, node_id: str) -> str:
        return _compact_slug(node_id, prefix_len=28)

    def _phase_branch_name(self, phase: str) -> str:
        return f"ralphite/{self._run_key()}/{self._phase_key(phase)}"

    def _worker_branch_name(self, phase_branch: str, node_id: str) -> str:
        return f"{phase_branch}--{self._node_key(node_id)}"

    def _phase_worktrees_root(self, phase: str) -> Path:
        return self._worktrees_root() / self._run_key() / self._phase_key(phase)

    def _worker_worktree_path(self, phase: str, node_id: str) -> Path:
        return self._phase_worktrees_root(phase) / self._node_key(node_id)

    def _integration_worktree_path(self, phase: str) -> Path:
        return self._phase_worktrees_root(phase) / "integration"

    def _prune_empty_worktree_ancestors(self, path: Path) -> list[str]:
        messages: list[str] = []
        root = self._worktrees_root()
        current = path
        while current != root and current.is_relative_to(root):
            if not current.exists():
                current = current.parent
                continue
            try:
                next(current.iterdir())
                break
            except StopIteration:
                current.rmdir()
                messages.append(f"removed empty directory {current}")
                current = current.parent
        return messages

    def _cleanup_stale_managed_worktree(
        self, path: Path, *, branch: str = ""
    ) -> tuple[bool, list[str]]:
        messages: list[str] = []
        if not path.exists():
            return True, messages
        if not path.is_relative_to(self._worktrees_root()):
            return False, [f"refusing to remove non-managed worktree path {path}"]

        marker_cleanup = cleanup_managed_process_marker(path)
        if marker_cleanup.get("process_terminated"):
            messages.append(
                f"terminated stale backend process {marker_cleanup.get('pid')} for {path}"
            )
        elif marker_cleanup.get("marker_removed"):
            messages.append(f"cleared stale backend marker for {path}")

        removed = self._git(["worktree", "remove", "--force", str(path)], check=False)
        if removed.returncode == 0:
            messages.append(f"removed stale managed worktree {path}")
            messages.extend(self._prune_empty_worktree_ancestors(path.parent))
            return True, messages

        if path.exists():
            try:
                shutil.rmtree(path)
                messages.append(f"deleted stale worktree directory {path}")
                messages.extend(self._prune_empty_worktree_ancestors(path.parent))
            except OSError as exc:
                detail = removed.stderr.strip() or removed.stdout.strip() or str(exc)
                messages.append(f"stale worktree cleanup failed {path}: {detail}")
                return False, messages

        if branch and self._branch_exists(branch):
            pruned = self._git(["worktree", "prune"], check=False)
            if pruned.returncode == 0 and (
                pruned.stdout.strip() or pruned.stderr.strip()
            ):
                messages.append(pruned.stderr.strip() or pruned.stdout.strip())
        return True, messages

    def _head_commit_metadata(self, cwd: Path) -> dict[str, Any]:
        commit = self._git(["rev-parse", "HEAD"], cwd=cwd, check=False)
        commit_sha = commit.stdout.strip() if commit.returncode == 0 else ""
        changed_files: list[dict[str, str]] = []
        listing = self._git(
            ["show", "--name-status", "--format=", "HEAD"], cwd=cwd, check=False
        )
        if listing.returncode == 0:
            for raw in listing.stdout.splitlines():
                line = raw.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                status = parts[0]
                if status.startswith("R") and len(parts) >= 3:
                    changed_files.append(
                        {
                            "status": status,
                            "path": parts[2],
                            "previous_path": parts[1],
                        }
                    )
                    continue
                changed_files.append({"status": status, "path": parts[1]})
        return {"commit": commit_sha, "changed_files": changed_files}

    def _workspace_local_changes(self, cwd: Path | None = None) -> list[str]:
        status = self._git(
            ["status", "--porcelain", "--untracked-files=all"],
            cwd=cwd,
            check=False,
        )
        if status.returncode != 0:
            return []
        files: list[str] = []
        for raw in status.stdout.splitlines():
            line = raw.rstrip()
            if len(line) < 4:
                continue
            payload = line[3:]
            if " -> " in payload:
                payload = payload.split(" -> ", 1)[1]
            candidate = payload.strip()
            if candidate:
                files.append(candidate)
        return sorted(dict.fromkeys(files))

    def _phase_touched_files(self, branch: str) -> list[str]:
        diff = self._git(
            ["diff", "--name-only", f"{self.base_branch}...{branch}"], check=False
        )
        if diff.returncode != 0:
            return []
        return sorted(
            {line.strip() for line in diff.stdout.splitlines() if line.strip()}
        )

    def _parse_merge_blocked_files(self, output: str) -> list[str]:
        lines = output.splitlines()
        files: list[str] = []
        capture = False
        for raw in lines:
            line = raw.rstrip()
            lowered = line.lower().strip()
            if (
                "would be overwritten by merge" in lowered
                or "the following untracked working tree files would be overwritten by merge"
                in lowered
            ):
                capture = True
                continue
            if not capture:
                continue
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Please ") or stripped.startswith("Aborting"):
                break
            files.append(stripped)
        return sorted(dict.fromkeys(files))

    def _tracked_unmerged_files(self, worktree: Path) -> list[str]:
        result = self._git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=worktree,
            check=False,
        )
        if result.returncode != 0:
            return []
        return sorted(
            {line.strip() for line in result.stdout.splitlines() if line.strip()}
        )

    def _collect_merge_conflict_details(
        self,
        worktree: Path,
        *,
        output: str = "",
    ) -> dict[str, Any]:
        current_run_conflict_files = self._tracked_unmerged_files(worktree)
        blocking_files = self._parse_merge_blocked_files(output)
        conflict_files = (
            current_run_conflict_files if current_run_conflict_files else blocking_files
        )
        return {
            "conflict_files": conflict_files,
            "current_run_conflict_files": current_run_conflict_files,
            "blocking_files": blocking_files,
        }

    def pre_base_integration_check(self, phase_branch: str) -> dict[str, Any]:
        local_files = self._workspace_local_changes()
        phase_files = self._phase_touched_files(phase_branch)
        overlap_files = sorted(set(local_files).intersection(phase_files))
        return {
            "ok": len(overlap_files) == 0,
            "local_files": local_files,
            "phase_files": phase_files,
            "overlap_files": overlap_files,
        }

    def _conflict_next_commands(self, worktree: Path) -> list[str]:
        return [
            f"cd {_quote_shell_path(worktree)}",
            "git status",
            "git add <resolved-files>",
            "git commit -m 'resolve merge conflicts'",
            "Return to Ralphite recovery and resume.",
        ]

    def _collect_conflict_files(self, worktree: Path) -> list[str]:
        files: list[str] = []
        if not worktree.exists():
            return files
        try:
            for path in worktree.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception:  # noqa: BLE001
                    continue
                if "<<<<<<< " in text and "=======" in text and ">>>>>>> " in text:
                    files.append(str(path.relative_to(worktree)))
        except Exception:  # noqa: BLE001
            return files
        return sorted(set(files))

    def bootstrap_state(self, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_git_available()
        state = dict(existing or {})
        state.setdefault("base_branch", self.base_branch)
        state.setdefault("phases", {})
        state.setdefault("cleanup_paths", [])
        state.setdefault("cleanup_branches", [])
        state.setdefault("preserved_paths", [])
        state.setdefault("preserved_branches", [])
        state.setdefault("retained_work", [])
        return state

    def _preserved_paths(self, state: dict[str, Any]) -> set[str]:
        return {
            str(item)
            for item in self.bootstrap_state(state).get("preserved_paths", [])
            if str(item).strip()
        }

    def _preserved_branches(self, state: dict[str, Any]) -> set[str]:
        return {
            str(item)
            for item in self.bootstrap_state(state).get("preserved_branches", [])
            if str(item).strip()
        }

    def _branch_exists(self, branch: str) -> bool:
        if not branch:
            return False
        return self._git(["rev-parse", "--verify", branch], check=False).returncode == 0

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
            status = self._git(
                ["status", "--porcelain", "--untracked-files=all"],
                cwd=worktree,
                check=False,
            )
            if status.returncode == 0:
                info["status_porcelain"] = status.stdout
            diff = self._git(["diff", "--binary"], cwd=worktree, check=False)
            if diff.returncode == 0:
                info["diff"] = diff.stdout
            cached = self._git(
                ["diff", "--cached", "--binary"], cwd=worktree, check=False
            )
            if cached.returncode == 0:
                info["cached_diff"] = cached.stdout
            info.update(self._head_commit_metadata(worktree))
        if branch_name:
            info["branch_exists"] = self._branch_exists(branch_name)
            if not info.get("commit") and info["branch_exists"]:
                commit = self._git(["rev-parse", branch_name], check=False)
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
        key = (
            scope,
            phase,
            node_id,
            path_text,
            branch_text,
            reason,
        )
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
        summary = {
            "preserved_paths": [],
            "preserved_branches": [],
            "retained_work": [],
        }
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
        summary["preserved_paths"] = sorted(self._preserved_paths(state))
        summary["preserved_branches"] = sorted(self._preserved_branches(state))
        return summary

    def phase_cleanup_allowed(self, state: dict[str, Any], phase: str) -> bool:
        phase_state = self.prepare_phase(state, phase)
        if not bool(phase_state.get("integrated_to_base")):
            return False
        preserved_paths = self._preserved_paths(state)
        preserved_branches = self._preserved_branches(state)
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

    def prepare_phase(self, state: dict[str, Any], phase: str) -> dict[str, Any]:
        state = self.bootstrap_state(state)
        phases = state["phases"]
        if phase in phases:
            return phases[phase]

        phase_branch = self._phase_branch_name(phase)
        phase_state = {
            "phase_branch": phase_branch,
            "workers": {},
            "merged_workers": [],
            "integration_worktree": "",
            "integrated_to_base": False,
        }
        phases[phase] = phase_state

        if (
            self._git(["rev-parse", "--verify", phase_branch], check=False).returncode
            != 0
        ):
            create = self._git(
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
        branch = self._worker_branch_name(phase_state["phase_branch"], node_id)
        worktree = self._worker_worktree_path(phase, node_id)
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
        if self._git(["rev-parse", "--verify", branch], check=False).returncode != 0:
            created = self._git(
                ["branch", branch, phase_state["phase_branch"]], check=False
            )
            if created.returncode != 0:
                info["prepare_error"] = created.stderr.strip() or created.stdout.strip()
                return info

        if worktree.exists():
            cleaned, cleanup_messages = self._cleanup_stale_managed_worktree(
                worktree, branch=branch
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

        added = self._git(
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
        if not self.git_available:
            return False, git_required_details(self.workspace_root)
        info = self.prepare_worker(state, phase, node_id)
        if info.get("prepare_error"):
            return False, {
                "reason": "worktree_prepare_failed",
                "error": info["prepare_error"],
            }

        worktree = Path(info["worktree_path"])
        add = self._git(["add", "-A"], cwd=worktree, check=False)
        if add.returncode != 0:
            return False, {
                "reason": "git_add_failed",
                "error": add.stderr.strip() or add.stdout.strip(),
            }

        commit = self._git(
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
            **self._head_commit_metadata(worktree),
        }

    def _ensure_integration_worktree(
        self, state: dict[str, Any], phase: str
    ) -> tuple[bool, dict[str, Any]]:
        phase_state = self.prepare_phase(state, phase)
        integration_path = self._integration_worktree_path(phase)
        integration_path.parent.mkdir(parents=True, exist_ok=True)
        phase_state["integration_worktree"] = str(integration_path)
        if integration_path.exists():
            return True, {
                "phase_branch": phase_state["phase_branch"],
                "worktree": str(integration_path),
            }

        add_wt = self._git(
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

    def prepare_phase_integration(
        self, state: dict[str, Any], phase: str
    ) -> tuple[str, dict[str, Any]]:
        phase_state = self.prepare_phase(state, phase)
        workers = phase_state.get("workers", {})
        worker_branches = [
            entry["branch"] for entry in workers.values() if entry.get("committed")
        ]

        if self._simulate_conflict(phase):
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

        ok, integration_meta = self._ensure_integration_worktree(state, phase)
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
            merged = self._git(
                ["merge", "--no-ff", "--no-edit", branch],
                cwd=integration_path,
                check=False,
            )
            if merged.returncode != 0:
                merge_output = merged.stderr.strip() or merged.stdout.strip()
                conflict_details = self._collect_merge_conflict_details(
                    integration_path, output=merge_output
                )
                return (
                    "recovery_required",
                    {
                        "reason": "worker_merge_conflict",
                        "phase": phase,
                        "branch": branch,
                        "error": merge_output,
                        "worktree": str(integration_path),
                        **conflict_details,
                        "next_commands": self._conflict_next_commands(integration_path),
                    },
                )
            merged_workers.append(branch)

        return "success", {
            "phase_branch": phase_state["phase_branch"],
            "workers": worker_branches,
            "worktree": str(integration_path),
        }

    def commit_phase_integration_changes(
        self, state: dict[str, Any], phase: str, message: str
    ) -> tuple[bool, dict[str, Any]]:
        status, details = self.prepare_phase_integration(state, phase)
        if status != "success":
            return False, details

        integration_path = Path(str(details.get("worktree") or ""))
        add = self._git(["add", "-A"], cwd=integration_path, check=False)
        if add.returncode != 0:
            return False, {
                "reason": "git_add_failed",
                "error": add.stderr.strip() or add.stdout.strip(),
            }

        has_staged = self._git(["diff", "--cached", "--quiet"], cwd=integration_path)
        if has_staged.returncode == 0:
            return True, {
                "mode": "noop",
                "message": "no phase integration changes to commit",
                "worktree": str(integration_path),
                "phase_branch": str(details.get("phase_branch") or ""),
            }

        commit = self._git(
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
            **self._head_commit_metadata(integration_path),
        }

    def _simulate_conflict(self, phase: str) -> bool:
        marker_path = self.workspace_root / ".ralphite" / "force_merge_conflict"
        marker = (
            marker_path.read_text(encoding="utf-8").strip()
            if marker_path.exists()
            else ""
        )
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

        precheck = self.pre_base_integration_check(phase_state["phase_branch"])
        if not bool(precheck.get("ok")):
            return (
                "recovery_required",
                {
                    "reason": "base_integration_blocked_by_local_changes",
                    "phase": phase,
                    "branch": phase_state["phase_branch"],
                    "worktree": str(self.workspace_root),
                    "overlap_files": list(precheck.get("overlap_files") or []),
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

        merged_to_base = self._git(
            ["merge", "--no-ff", "--no-edit", phase_state["phase_branch"]], check=False
        )
        if merged_to_base.returncode != 0:
            merge_output = (
                merged_to_base.stderr.strip() or merged_to_base.stdout.strip()
            )
            conflict_details = self._collect_merge_conflict_details(
                self.workspace_root, output=merge_output
            )
            return (
                "recovery_required",
                {
                    "reason": "base_merge_conflict",
                    "phase": phase,
                    "branch": phase_state["phase_branch"],
                    "error": merge_output,
                    "worktree": str(self.workspace_root),
                    **conflict_details,
                    "next_commands": self._conflict_next_commands(self.workspace_root),
                },
            )

        phase_state["integrated_to_base"] = True
        return "success", {
            "phase_branch": phase_state["phase_branch"],
            "workers": worker_branches,
        }

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

    def detect_stale_artifacts(
        self,
        active_run_ids: list[str],
        max_age_hours: int = 24,
    ) -> dict[str, list[dict[str, Any]]]:
        now = datetime.now(timezone.utc)
        active = set(active_run_ids)
        active_short = {run_id[:8] for run_id in active_run_ids}
        threshold_seconds = max(0, max_age_hours) * 3600

        stale_worktrees: list[dict[str, Any]] = []
        root = self._worktrees_root()
        if root.exists():
            for run_dir in root.iterdir():
                if not run_dir.is_dir():
                    continue
                run_key = run_dir.name
                age = (
                    now
                    - datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
                ).total_seconds()
                orphan = run_key not in active and run_key[:8] not in active_short
                if orphan and age > threshold_seconds:
                    stale_worktrees.append(
                        {
                            "run_id": run_key,
                            "path": str(run_dir),
                            "age_hours": round(age / 3600, 2),
                            "reason": "stale_worktree_root",
                        }
                    )

        stale_branches: list[dict[str, Any]] = []
        if self.git_available:
            result = self._git(["branch", "--list", "ralphite/*"], check=False)
            if result.returncode == 0:
                for raw in result.stdout.splitlines():
                    branch = raw.strip().lstrip("* ").strip()
                    if not branch:
                        continue
                    parts = branch.split("/")
                    run_key = parts[1] if len(parts) > 1 else ""
                    if (
                        run_key
                        and run_key not in active
                        and run_key not in active_short
                    ):
                        stale_branches.append(
                            {
                                "run_id": run_key,
                                "branch": branch,
                                "reason": "orphan_managed_branch",
                            }
                        )

        return {
            "stale_worktrees": stale_worktrees,
            "stale_branches": stale_branches,
        }

    def managed_artifact_inventory(self, run_id: str | None = None) -> dict[str, Any]:
        self._ensure_git_available()
        run_ref = str(run_id or self.run_id)
        run_key = self._run_key_for(run_ref)
        branch_prefix = f"ralphite/{run_key}"

        branches: list[dict[str, Any]] = []
        result = self._git(["branch", "--list", f"{branch_prefix}*"], check=False)
        if result.returncode == 0:
            for raw in result.stdout.splitlines():
                branch = raw.strip().lstrip("* ").strip()
                if not branch:
                    continue
                commit = self._git(["rev-parse", branch], check=False)
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
        listing = self._git(["worktree", "list", "--porcelain"], check=False)
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

    def cleanup_orphaned_run_artifacts(self, run_id: str | None = None) -> list[str]:
        self._ensure_git_available()
        inventory = self.managed_artifact_inventory(run_id)
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
            removed = self._git(["worktree", "remove", "--force", path], check=False)
            if removed.returncode == 0:
                messages.append(f"removed worktree {path}")
                messages.extend(self._prune_empty_worktree_ancestors(path_obj.parent))
            else:
                messages.append(
                    f"worktree remove skipped {path}: {removed.stderr.strip() or removed.stdout.strip()}"
                )

        pruned = self._git(["worktree", "prune"], check=False)
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
            exists = self._git(["rev-parse", "--verify", branch], check=False)
            if exists.returncode != 0:
                messages.append(f"branch already removed {branch}")
                continue
            deleted = self._git(["branch", "-D", branch], check=False)
            if deleted.returncode == 0:
                messages.append(f"deleted branch {branch}")
            else:
                messages.append(
                    f"branch delete skipped {branch}: {deleted.stderr.strip() or deleted.stdout.strip()}"
                )

        self._git(["worktree", "prune"], check=False)
        return messages

    def cleanup_phase(
        self, state: dict[str, Any], phase: str, *, discard_preserved: bool = False
    ) -> list[str]:
        self._ensure_git_available()
        messages: list[str] = []
        phase_state = self.prepare_phase(state, phase)
        removed_paths: list[str] = []
        removed_branches: list[str] = []
        preserved_paths = set() if discard_preserved else self._preserved_paths(state)
        preserved_branches = (
            set() if discard_preserved else self._preserved_branches(state)
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
                messages.extend(self._prune_empty_worktree_ancestors(path_obj.parent))
                continue
            removed = self._git(["worktree", "remove", "--force", path], check=False)
            if removed.returncode == 0:
                messages.append(f"removed worktree {path}")
                removed_paths.append(path)
                messages.extend(self._prune_empty_worktree_ancestors(path_obj.parent))
            else:
                messages.append(
                    f"worktree remove skipped {path}: {removed.stderr.strip() or removed.stdout.strip()}"
                )

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
            exists = self._git(["rev-parse", "--verify", branch], check=False)
            if exists.returncode != 0:
                messages.append(f"branch already removed {branch}")
                continue
            deleted = self._git(["branch", "-D", branch], check=False)
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
        self._ensure_git_available()
        messages: list[str] = []
        phases = list(self.bootstrap_state(state).get("phases", {}).keys())
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
        if not self.git_available:
            return False, git_required_details(self.workspace_root)

        if paths:
            staged_paths: list[str] = []
            for raw_path in paths:
                path_obj = Path(raw_path).expanduser()
                if path_obj.is_absolute():
                    try:
                        path_obj = path_obj.resolve().relative_to(self.workspace_root)
                    except Exception:  # noqa: BLE001
                        continue
                add_one = self._git(["add", "--", str(path_obj)], check=False)
                if add_one.returncode != 0:
                    return False, {
                        "reason": "git_add_failed",
                        "path": str(path_obj),
                        "error": add_one.stderr.strip() or add_one.stdout.strip(),
                    }
                staged_paths.append(str(path_obj))

            has_staged = self._git(["diff", "--cached", "--quiet"], check=False)
            if has_staged.returncode == 0:
                return True, {
                    "mode": "noop",
                    "message": "no staged changes to commit",
                    "paths": staged_paths,
                }
        else:
            status = self._git(["status", "--porcelain"], check=False)
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

            add = self._git(["add", "-A"], check=False)
            if add.returncode != 0:
                return False, {
                    "reason": "git_add_failed",
                    "error": add.stderr.strip() or add.stdout.strip(),
                }

        commit = self._git(["commit", "-m", message], check=False)
        if commit.returncode != 0:
            return False, {
                "reason": "git_commit_failed",
                "error": commit.stderr.strip() or commit.stdout.strip(),
            }

        return True, {
            "mode": "committed",
            "message": message,
            "paths": list(paths or []),
            **self._head_commit_metadata(self.workspace_root),
        }
