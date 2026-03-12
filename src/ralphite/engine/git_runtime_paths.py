from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralphite.engine.git_worktree import GitWorktreeManager


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


class GitRuntimePaths:
    def __init__(self, manager: "GitWorktreeManager") -> None:
        self.manager = manager

    @property
    def context(self):  # noqa: ANN201
        return self.manager.context

    def worktrees_root(self) -> Path:
        return self.context.workspace_root / ".ralphite" / "worktrees"

    def run_key(self) -> str:
        run_id = self.context.run_id
        return _compact_slug(run_id[:8] or run_id, prefix_len=12)

    def run_key_for(self, run_id: str | None = None) -> str:
        candidate = str(run_id or self.context.run_id)
        return _compact_slug(candidate[:8] or candidate, prefix_len=12)

    def phase_key(self, phase: str) -> str:
        return _compact_slug(phase, prefix_len=20)

    def node_key(self, node_id: str) -> str:
        return _compact_slug(node_id, prefix_len=28)

    def phase_branch_name(self, phase: str) -> str:
        return f"ralphite/{self.run_key()}/{self.phase_key(phase)}"

    def worker_branch_name(self, phase_branch: str, node_id: str) -> str:
        return f"{phase_branch}--{self.node_key(node_id)}"

    def phase_worktrees_root(self, phase: str) -> Path:
        return self.worktrees_root() / self.run_key() / self.phase_key(phase)

    def worker_worktree_path(self, phase: str, node_id: str) -> Path:
        return self.phase_worktrees_root(phase) / self.node_key(node_id)

    def integration_worktree_path(self, phase: str) -> Path:
        return self.phase_worktrees_root(phase) / "integration"

    def prune_empty_worktree_ancestors(self, path: Path) -> list[str]:
        messages: list[str] = []
        root = self.worktrees_root()
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
