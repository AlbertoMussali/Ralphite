from __future__ import annotations

from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ralphite.engine.git_worktree import GitWorktreeManager


class GitRuntimeRepo:
    def __init__(self, manager: "GitWorktreeManager") -> None:
        self.manager = manager

    @property
    def context(self):  # noqa: ANN201
        return self.manager.context

    def detect_git_workspace(self) -> bool:
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.context.workspace_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return False
        return True

    def detect_base_branch(self) -> str:
        result = self.manager._git(["symbolic-ref", "--short", "HEAD"], check=False)
        if result.returncode == 0:
            name = result.stdout.strip()
            if name:
                return name
        return "main"

    def repository_status(self) -> dict[str, Any]:
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.context.workspace_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return {
                "ok": False,
                "reason": "git_required",
                "workspace_root": str(self.context.workspace_root),
                "detail": "workspace must be inside a git worktree for Ralphite execution",
                "remediation": "git init -b main",
            }

        result = self.manager._git(["rev-parse", "HEAD"], check=False)
        if result.returncode != 0:
            return {
                "ok": False,
                "reason": "git_required",
                "workspace_root": str(self.context.workspace_root),
                "detail": "git repo has no initial commit",
                "remediation": 'git add -A && git commit -m "initial workspace state"',
            }

        status = self.manager._git(["status", "--porcelain"], check=False)
        return {
            "ok": True,
            "workspace_root": str(self.context.workspace_root),
            "base_branch": self.context.base_branch,
            "dirty": status.returncode == 0 and bool(status.stdout.strip()),
            "detail": f"git worktree detected (base branch: {self.context.base_branch})",
        }

    def execution_status(self) -> dict[str, Any]:
        repo = self.repository_status()
        if not bool(repo.get("ok")):
            return repo
        if bool(repo.get("dirty")):
            return {
                "ok": False,
                "reason": "git_required",
                "workspace_root": str(self.context.workspace_root),
                "base_branch": self.context.base_branch,
                "dirty": True,
                "detail": "worktree is dirty in a blocking way",
                "remediation": 'git add -A && git commit -m "save state"',
            }
        return {**repo, "dirty": False}

    def head_commit_metadata(self, cwd: Path) -> dict[str, Any]:
        commit = self.manager._git(["rev-parse", "HEAD"], cwd=cwd, check=False)
        commit_sha = commit.stdout.strip() if commit.returncode == 0 else ""
        changed_files: list[dict[str, str]] = []
        listing = self.manager._git(
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

    def workspace_local_changes(self, cwd: Path | None = None) -> list[str]:
        status = self.manager._git(
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

    def phase_touched_files(self, branch: str) -> list[str]:
        diff = self.manager._git(
            ["diff", "--name-only", f"{self.context.base_branch}...{branch}"],
            check=False,
        )
        if diff.returncode != 0:
            return []
        return sorted(
            {line.strip() for line in diff.stdout.splitlines() if line.strip()}
        )

    def normalize_rel_path(self, raw_path: str | Path) -> str:
        candidate = Path(str(raw_path)).expanduser()
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(self.context.workspace_root)
            except Exception:
                return ""
        normalized = str(candidate).replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    def normalize_rel_paths(self, raw_paths: list[str] | tuple[str, ...]) -> set[str]:
        return {
            normalized
            for item in raw_paths
            if (normalized := self.normalize_rel_path(item))
        }

    def branch_exists(self, branch: str) -> bool:
        if not branch:
            return False
        return (
            self.manager._git(["rev-parse", "--verify", branch], check=False).returncode
            == 0
        )
