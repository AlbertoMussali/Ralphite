from __future__ import annotations

from datetime import datetime, timezone
import shutil
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from ralphite.engine.process_guard import cleanup_managed_process_marker

if TYPE_CHECKING:
    from ralphite.engine.git_worktree import GitWorktreeManager


class GitRuntimeConflicts:
    def __init__(self, manager: "GitWorktreeManager") -> None:
        self.manager = manager

    @property
    def context(self):  # noqa: ANN201
        return self.manager.context

    def classify_delete_failure(self, path: Path, detail: str) -> str:
        lowered = detail.lower()
        if (
            "permission denied" in lowered
            or "being used by another process" in lowered
            or "device or resource busy" in lowered
            or "winerror 32" in lowered
            or "winerror 5" in lowered
        ):
            return "transient_lock"
        if (
            "filename or extension is too long" in lowered
            or "path too long" in lowered
            or "name too long" in lowered
            or len(str(path)) >= 220
        ):
            return "long_path_risk"
        return "unknown_filesystem_error"

    def delete_tree_with_retry(
        self, path: Path, *, branch: str = "", stale: bool = False
    ) -> tuple[bool, list[str]]:
        messages: list[str] = []
        label = "stale managed worktree" if stale else "worktree"
        last_detail = ""
        for attempt in range(1, 4):
            removed = self.manager._git(
                ["worktree", "remove", "--force", str(path)], check=False
            )
            if removed.returncode == 0:
                messages.append(f"removed {label} {path}")
                messages.extend(
                    self.manager._paths.prune_empty_worktree_ancestors(path.parent)
                )
                return True, messages
            last_detail = removed.stderr.strip() or removed.stdout.strip()
            if branch:
                pruned = self.manager._git(["worktree", "prune"], check=False)
                prune_detail = pruned.stderr.strip() or pruned.stdout.strip()
                if prune_detail:
                    messages.append(prune_detail)
            if not path.exists():
                messages.append(f"missing {label} metadata will be pruned {path}")
                return True, messages
            if attempt < 3:
                time.sleep(0.1 * attempt)

        for attempt in range(1, 4):
            if not path.exists():
                messages.append(f"missing {label} metadata will be pruned {path}")
                return True, messages
            try:
                shutil.rmtree(path)
                messages.append(f"deleted {label} directory {path}")
                messages.extend(
                    self.manager._paths.prune_empty_worktree_ancestors(path.parent)
                )
                return True, messages
            except OSError as exc:
                last_detail = str(exc) or last_detail
                if branch:
                    self.manager._git(["worktree", "prune"], check=False)
                if attempt < 3:
                    time.sleep(0.1 * attempt)

        kind = self.classify_delete_failure(path, last_detail)
        messages.append(f"{label} cleanup failed {path}: [{kind}] {last_detail}")
        return False, messages

    def cleanup_stale_managed_worktree(
        self, path: Path, *, branch: str = ""
    ) -> tuple[bool, list[str]]:
        messages: list[str] = []
        if not path.exists():
            return True, messages
        if not path.is_relative_to(self.manager._paths.worktrees_root()):
            return False, [f"refusing to remove non-managed worktree path {path}"]

        marker_cleanup = cleanup_managed_process_marker(path)
        if marker_cleanup.get("process_terminated"):
            messages.append(
                f"terminated stale backend process {marker_cleanup.get('pid')} for {path}"
            )
        elif marker_cleanup.get("marker_removed"):
            messages.append(f"cleared stale backend marker for {path}")
        removed, remove_messages = self.remove_managed_worktree_path(
            path, branch=branch, stale=True
        )
        messages.extend(remove_messages)
        return removed, messages

    def parse_merge_blocked_files(self, output: str) -> list[str]:
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

    def tracked_unmerged_files(self, worktree: Path) -> list[str]:
        result = self.manager._git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=worktree,
            check=False,
        )
        if result.returncode != 0:
            return []
        return sorted(
            {line.strip() for line in result.stdout.splitlines() if line.strip()}
        )

    def collect_merge_conflict_details(
        self, worktree: Path, *, output: str = ""
    ) -> dict[str, Any]:
        current_run_conflict_files = self.tracked_unmerged_files(worktree)
        blocking_files = self.parse_merge_blocked_files(output)
        conflict_files = (
            current_run_conflict_files if current_run_conflict_files else blocking_files
        )
        return {
            "conflict_files": conflict_files,
            "current_run_conflict_files": current_run_conflict_files,
            "blocking_files": blocking_files,
        }

    def remove_managed_worktree_path(
        self, path: Path, *, branch: str = "", stale: bool = False
    ) -> tuple[bool, list[str]]:
        if not path.is_relative_to(self.manager._paths.worktrees_root()):
            return False, [f"refusing to remove non-managed worktree path {path}"]
        return self.delete_tree_with_retry(path, branch=branch, stale=stale)

    def merge_conflict_blocks(
        self, text: str
    ) -> tuple[list[tuple[str, list[str], list[str]]], bool]:
        lines = text.splitlines(keepends=True)
        chunks: list[tuple[str, list[str], list[str]]] = []
        index = 0
        had_conflict = False
        while index < len(lines):
            if not lines[index].startswith("<<<<<<< "):
                chunks.append((lines[index], [], []))
                index += 1
                continue
            had_conflict = True
            index += 1
            ours: list[str] = []
            theirs: list[str] = []
            while index < len(lines) and not lines[index].startswith("======="):
                ours.append(lines[index])
                index += 1
            if index >= len(lines):
                return [], False
            index += 1
            while index < len(lines) and not lines[index].startswith(">>>>>>> "):
                theirs.append(lines[index])
                index += 1
            if index >= len(lines):
                return [], False
            index += 1
            chunks.append(("", ours, theirs))
        return chunks, had_conflict

    def merge_unique_lines(self, left: list[str], right: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for bucket in (left, right):
            for line in bucket:
                key = line.rstrip("\n")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(line)
        return merged

    def conflict_resolver_kind(
        self, path: Path, ours: list[str], theirs: list[str]
    ) -> str | None:
        ours_nonempty = [line.strip() for line in ours if line.strip()]
        theirs_nonempty = [line.strip() for line in theirs if line.strip()]
        if not ours_nonempty or not theirs_nonempty:
            return None
        if all(line.startswith("export ") for line in ours_nonempty + theirs_nonempty):
            return "additive_exports"
        if path.suffix.lower() in {".md"} and all(
            not any(token in line for token in ("{", "}", ";", "=>"))
            for line in ours_nonempty + theirs_nonempty
        ):
            return "append_only_lines"
        return None

    def auto_resolve_conflict_file(self, path: Path) -> tuple[bool, dict[str, Any]]:
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, {"error": str(exc)}
        chunks, had_conflict = self.merge_conflict_blocks(original)
        if not had_conflict:
            return False, {"error": "no merge conflict markers found"}

        resolved_parts: list[str] = []
        resolver_kinds: set[str] = set()
        for plain, ours, theirs in chunks:
            if not ours and not theirs:
                resolved_parts.append(plain)
                continue
            resolver_kind = self.conflict_resolver_kind(path, ours, theirs)
            if not resolver_kind:
                return False, {"error": "unsupported conflict shape"}
            resolver_kinds.add(resolver_kind)
            resolved_parts.extend(self.merge_unique_lines(ours, theirs))
        path.write_text("".join(resolved_parts), encoding="utf-8")
        return True, {"resolver_kind": "+".join(sorted(resolver_kinds))}

    def collect_conflict_files(self, worktree: Path) -> list[str]:
        files: list[str] = []
        if not worktree.exists():
            return files
        try:
            for path in worktree.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception:
                    continue
                if "<<<<<<< " in text and "=======" in text and ">>>>>>> " in text:
                    files.append(str(path.relative_to(worktree)))
        except Exception:
            return files
        return sorted(set(files))

    def attempt_auto_resolve_merge_conflicts(
        self, worktree: Path
    ) -> tuple[bool, dict[str, Any]]:
        conflict_files = self.tracked_unmerged_files(
            worktree
        ) or self.collect_conflict_files(worktree)
        if not conflict_files:
            return False, {
                "resolver_attempted": False,
                "resolved_files": [],
                "unsupported_conflict_files": [],
            }

        resolved_files: list[str] = []
        resolver_kinds: list[str] = []
        unsupported_files: list[str] = []
        for rel_path in conflict_files:
            file_path = worktree / rel_path
            resolved, details = self.auto_resolve_conflict_file(file_path)
            if not resolved:
                unsupported_files.append(rel_path)
                continue
            add = self.manager._git(["add", "--", rel_path], cwd=worktree, check=False)
            if add.returncode != 0:
                unsupported_files.append(rel_path)
                continue
            resolved_files.append(rel_path)
            resolver_kind = str(details.get("resolver_kind") or "").strip()
            if resolver_kind:
                resolver_kinds.append(resolver_kind)

        if unsupported_files:
            return False, {
                "resolver_attempted": True,
                "resolved_files": resolved_files,
                "unsupported_conflict_files": sorted(dict.fromkeys(unsupported_files)),
            }

        commit = self.manager._git(["commit", "--no-edit"], cwd=worktree, check=False)
        if commit.returncode != 0:
            return False, {
                "resolver_attempted": True,
                "resolved_files": resolved_files,
                "unsupported_conflict_files": conflict_files,
                "resolver_commit_error": commit.stderr.strip() or commit.stdout.strip(),
            }

        return True, {
            "resolver_attempted": True,
            "resolved_files": resolved_files,
            "resolver_kind": "+".join(sorted(dict.fromkeys(resolver_kinds))),
            "auto_resolved_conflicts": resolved_files,
            **self.manager._repo.head_commit_metadata(worktree),
        }

    def pre_base_integration_check(
        self, phase_branch: str, *, ignore_paths: list[str] | None = None
    ) -> dict[str, Any]:
        local_files = self.manager._repo.workspace_local_changes()
        phase_files = self.manager._repo.phase_touched_files(phase_branch)
        ignored = self.manager._repo.normalize_rel_paths(list(ignore_paths or []))
        ignored_overlap_files = sorted(
            set(local_files).intersection(phase_files).intersection(ignored)
        )
        overlap_files = sorted(
            set(local_files).intersection(phase_files).difference(ignored)
        )
        return {
            "ok": len(overlap_files) == 0,
            "local_files": local_files,
            "phase_files": phase_files,
            "overlap_files": overlap_files,
            "ignored_overlap_files": ignored_overlap_files,
        }

    def conflict_next_commands(self, worktree: Path) -> list[str]:
        return [
            f"cd {self.manager._quote_shell_path(worktree)}",
            "git status",
            "git add <resolved-files>",
            "git commit -m 'resolve merge conflicts'",
            "Return to Ralphite recovery and resume.",
        ]

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
        root = self.manager._paths.worktrees_root()
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
        if self.manager.git_available:
            result = self.manager._git(["branch", "--list", "ralphite/*"], check=False)
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
