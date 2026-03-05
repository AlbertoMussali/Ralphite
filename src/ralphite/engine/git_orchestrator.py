from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ralphite.engine.git_worktree import GitWorktreeManager

logger = logging.getLogger(__name__)


class GitOrchestrator:
    """Manages git states and worktree operations for the orchestrator runtime."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()

    def get_manager(self, run_id: str) -> GitWorktreeManager:
        """Create a GitWorktreeManager bounded to a specific run."""
        return GitWorktreeManager(self.workspace_root, run_id)

    def bootstrap_state(
        self, run_id: str, current_state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Bootstrap or update the initial git state for a run."""
        return self.get_manager(run_id).bootstrap_state(current_state)

    def cleanup_run(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """Cleanup all git resources for a run."""
        return self.get_manager(run_id).cleanup_all(state)

    def detect_stale_artifacts(self, max_age_hours: int = 24) -> list[str]:
        """Identify stale worktrees across all runs."""
        return self.get_manager("doctor").detect_stale_artifacts(
            max_age_hours=max_age_hours
        )

    def resolve_worker_worktree(self, commit_meta: dict[str, Any]) -> Path:
        """Resolve the active worktree path for a worker task execution."""
        raw = commit_meta.get("worktree") if isinstance(commit_meta, dict) else None
        if isinstance(raw, str) and raw.strip():
            candidate = Path(raw).expanduser().resolve()
            if candidate.exists() and candidate.is_dir():
                return candidate
        return self.workspace_root

    def is_worktree_relative_glob(self, path_glob: str) -> bool:
        """Check if a glob string is safely bound to the worktree."""
        value = (path_glob or "").strip()
        if not value:
            return False
        if value.startswith("/") or value.startswith("\\"):
            return False
        if (
            len(value) > 2
            and value[1] == ":"
            and value[0].isalpha()
            and value[2] in {"\\", "/"}
        ):
            return False
        normalized_parts = value.replace("\\", "/").split("/")
        if any(part == ".." for part in normalized_parts):
            return False
        return True
