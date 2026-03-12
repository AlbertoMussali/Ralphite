from __future__ import annotations

import glob
import os
from pathlib import Path
import shlex
import subprocess
from typing import TYPE_CHECKING, Any

from ralphite.engine.git_orchestrator import GitOrchestrator
from ralphite.engine.git_worktree import GitWorktreeManager
from ralphite.engine.headless_agent import (
    BackendExecutionConfig,
    build_node_prompt,
    build_worker_subprocess_env,
    execute_headless_agent,
)
from ralphite.engine.templates import versioned_filename
from ralphite.schemas.plan import AgentSpec, BehaviorKind
from ralphite.engine.structure_compiler import RuntimeNodeSpec

if TYPE_CHECKING:
    from ralphite.engine.config import LocalConfig
    from ralphite.engine.orchestrator import RuntimeHandle


class RuntimeNodeRunner:
    def __init__(
        self,
        *,
        workspace_root: Path,
        config: "LocalConfig",
        git_orchestrator: GitOrchestrator,
        execute_agent_callback: Any | None = None,
        evaluate_acceptance_callback: Any | None = None,
        build_worker_env: Any | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.config = config
        self.git_orchestrator = git_orchestrator
        self.execute_agent_callback = execute_agent_callback
        self.evaluate_acceptance_callback = evaluate_acceptance_callback
        self.build_worker_env = build_worker_env or build_worker_subprocess_env

    def tool_allowed(self, tool_id: str, snapshot: dict[str, list[str]]) -> bool:
        deny = set(snapshot.get("deny_tools", []))
        allow = set(snapshot.get("allow_tools", []))
        if tool_id in deny:
            return False
        if not allow or "tool:*" in allow:
            return True
        return tool_id in allow

    def mcp_allowed(self, mcp_id: str, snapshot: dict[str, list[str]]) -> bool:
        deny = set(snapshot.get("deny_mcps", []))
        allow = set(snapshot.get("allow_mcps", []))
        if mcp_id in deny:
            return False
        if not allow or "mcp:*" in allow:
            return True
        return mcp_id in allow

    def resolve_execution_defaults(
        self, handle: "RuntimeHandle", profile: AgentSpec
    ) -> tuple[str, str, str, str]:
        defaults = (
            handle.run.metadata.get("execution_defaults")
            if isinstance(handle.run.metadata.get("execution_defaults"), dict)
            else {}
        )
        backend_raw = (
            str(
                defaults.get("backend")
                or profile.provider.value
                or self.config.default_backend
                or "codex"
            )
            .strip()
            .lower()
        )
        if backend_raw not in {"codex", "cursor"}:
            backend_raw = "codex"

        model_raw = str(
            defaults.get("model")
            or profile.model
            or self.config.default_model
            or "gpt-5.3-codex"
        ).strip()
        model = model_raw or "gpt-5.3-codex"

        reasoning_raw = (
            str(
                defaults.get("reasoning_effort")
                or profile.reasoning_effort.value
                or self.config.default_reasoning_effort
                or "medium"
            )
            .strip()
            .lower()
        )
        reasoning_effort = (
            reasoning_raw if reasoning_raw in {"low", "medium", "high"} else "medium"
        )
        cursor_command = (
            str(
                defaults.get("cursor_command") or self.config.cursor_command or "agent"
            ).strip()
            or "agent"
        )
        return backend_raw, model, reasoning_effort, cursor_command

    def execute_agent_impl(
        self,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        profile: AgentSpec,
        snapshot: dict[str, list[str]],
        *,
        worktree: Path,
    ) -> tuple[bool, dict[str, Any]]:
        requested = list(profile.tools_allow or [])
        denied: list[str] = []
        for item in requested:
            if item.startswith("tool:") and not self.tool_allowed(item, snapshot):
                denied.append(item)
            if item.startswith("mcp:") and not self.mcp_allowed(item, snapshot):
                denied.append(item)

        if denied:
            return False, {"reason": "permission_denied", "denied": denied}

        task = str(node.task or "")
        if "[fail]" in task.lower():
            return False, {"reason": "task_marker_failure", "task": task}

        backend, model, reasoning_effort, cursor_command = (
            self.resolve_execution_defaults(handle, profile)
        )
        try:
            prompt = build_node_prompt(
                node,
                worktree=worktree,
                permission_snapshot=snapshot,
                plan_id=handle.plan.plan_id,
                plan_name=handle.plan.name,
                agent_id=profile.id,
                agent_role=profile.role.value,
                system_prompt=profile.system_prompt,
                behavior_prompt_template=node.behavior_prompt_template,
                write_policy=self.node_write_policy(handle, node),
            )
        except ValueError as exc:
            return False, {
                "reason": "defaults.placeholder_invalid",
                "error": str(exc),
                "agent_id": profile.id,
                "role": node.role,
            }
        ok, result = execute_headless_agent(
            config=BackendExecutionConfig(
                backend=backend,
                model=model,
                reasoning_effort=reasoning_effort,
                cursor_command=cursor_command,
                timeout_seconds=max(
                    60, int(handle.plan.constraints.max_runtime_seconds)
                ),
            ),
            prompt=prompt,
            worktree=worktree,
        )
        if not ok:
            return False, result
        return True, {
            **result,
            "agent_id": profile.id,
            "provider": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "role": node.role,
            "phase": node.phase,
            "lane": node.lane,
        }

    def node_surfaces(self, handle: "RuntimeHandle", node: RuntimeNodeSpec) -> set[str]:
        node_surface_map = (
            handle.run.metadata.get("node_surface_map", {})
            if isinstance(handle.run.metadata.get("node_surface_map"), dict)
            else {}
        )
        surfaces = node_surface_map.get(node.id, [])
        if not isinstance(surfaces, list):
            return set()
        return {str(item).strip().lower() for item in surfaces if str(item).strip()}

    def node_write_policy(
        self, handle: "RuntimeHandle", node: RuntimeNodeSpec
    ) -> dict[str, Any]:
        node_write_policy_map = (
            handle.run.metadata.get("node_write_policy_map", {})
            if isinstance(handle.run.metadata.get("node_write_policy_map"), dict)
            else {}
        )
        raw = (
            node_write_policy_map.get(node.id, {})
            if isinstance(node_write_policy_map.get(node.id, {}), dict)
            else {}
        )
        return {
            "allowed_write_roots": [
                str(item).strip().strip("/\\")
                for item in raw.get("allowed_write_roots", [])
                if str(item).strip()
            ],
            "forbidden_write_roots": [
                str(item).strip().strip("/\\")
                for item in raw.get("forbidden_write_roots", [])
                if str(item).strip()
            ],
            "allow_plan_edits": bool(raw.get("allow_plan_edits")),
            "allow_root_writes": bool(raw.get("allow_root_writes")),
        }

    def snapshot_changed_files(self, snapshot: dict[str, Any]) -> list[str]:
        files: list[str] = []
        status_porcelain = (
            str(snapshot.get("status_porcelain") or "")
            if isinstance(snapshot, dict)
            else ""
        )
        for raw in status_porcelain.splitlines():
            if len(raw) < 4:
                continue
            payload = raw[3:]
            if " -> " in payload:
                payload = payload.split(" -> ", 1)[1]
            candidate = payload.strip()
            if candidate:
                files.append(candidate)
        if files:
            return sorted(dict.fromkeys(files))
        changed = snapshot.get("changed_files") if isinstance(snapshot, dict) else []
        if isinstance(changed, list):
            for item in changed:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                if path:
                    files.append(path)
        return sorted(dict.fromkeys(files))

    def classify_write_scope(
        self,
        *,
        changed_files: list[str],
        write_policy: dict[str, Any],
        plan_path: str,
    ) -> dict[str, Any]:
        normalized_changed = [
            str(item).strip().replace("\\", "/").lstrip("./")
            for item in changed_files
            if str(item).strip()
        ]
        allowed_roots = {
            str(item).strip().replace("\\", "/").strip("/")
            for item in write_policy.get("allowed_write_roots", [])
            if str(item).strip()
        }
        forbidden_roots = {
            str(item).strip().replace("\\", "/").strip("/")
            for item in write_policy.get("forbidden_write_roots", [])
            if str(item).strip()
        }
        allow_plan_edits = bool(write_policy.get("allow_plan_edits"))
        allow_root_writes = bool(write_policy.get("allow_root_writes"))
        plan_rel = ""
        try:
            plan_rel = str(
                Path(plan_path).expanduser().resolve().relative_to(self.workspace_root)
            ).replace("\\", "/")
        except Exception:
            plan_rel = str(plan_path or "").replace("\\", "/").lstrip("./")

        in_scope: list[str] = []
        out_of_scope: list[str] = []
        plan_edits: list[str] = []
        forbidden_hits: list[str] = []
        for path in normalized_changed:
            if not path:
                continue
            if plan_rel and path == plan_rel:
                if allow_plan_edits:
                    in_scope.append(path)
                else:
                    plan_edits.append(path)
                continue
            if any(
                path == root or path.startswith(f"{root}/")
                for root in forbidden_roots
                if root
            ):
                forbidden_hits.append(path)
                continue
            if allow_root_writes or not allowed_roots:
                in_scope.append(path)
                continue
            if any(
                path == root or path.startswith(f"{root}/")
                for root in allowed_roots
                if root
            ):
                in_scope.append(path)
                continue
            out_of_scope.append(path)
        return {
            "changed_files": normalized_changed,
            "in_scope_files": sorted(dict.fromkeys(in_scope)),
            "out_of_scope_files": sorted(dict.fromkeys(out_of_scope)),
            "plan_edit_files": sorted(dict.fromkeys(plan_edits)),
            "forbidden_files": sorted(dict.fromkeys(forbidden_hits)),
            "allowed_write_roots": sorted(allowed_roots),
            "forbidden_write_roots": sorted(forbidden_roots),
            "allow_plan_edits": allow_plan_edits,
            "allow_root_writes": allow_root_writes,
            "observed_out_of_scope_mutation": bool(
                out_of_scope or plan_edits or forbidden_hits
            ),
        }

    def collect_worker_evidence(
        self,
        *,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
        worktree_path: str,
    ) -> dict[str, Any]:
        snapshot = git_manager.inspect_managed_target(
            worktree_path=worktree_path or None
        )
        write_policy = self.node_write_policy(handle, node)
        scope = self.classify_write_scope(
            changed_files=self.snapshot_changed_files(snapshot),
            write_policy=write_policy,
            plan_path=handle.run.plan_path,
        )
        diagnostics = {
            "worktree_path": str(snapshot.get("worktree_path") or worktree_path or ""),
            "worktree_exists": bool(snapshot.get("worktree_exists")),
            "status_porcelain": str(snapshot.get("status_porcelain") or ""),
            "changed_files": list(scope.get("changed_files", [])),
            "in_scope_files": list(scope.get("in_scope_files", [])),
            "out_of_scope_files": list(scope.get("out_of_scope_files", [])),
            "forbidden_files": list(scope.get("forbidden_files", [])),
            "plan_edit_files": list(scope.get("plan_edit_files", [])),
            "allowed_write_roots": list(scope.get("allowed_write_roots", [])),
            "forbidden_write_roots": list(scope.get("forbidden_write_roots", [])),
            "observed_out_of_scope_mutation": bool(
                scope.get("observed_out_of_scope_mutation")
            ),
        }
        return {
            "snapshot": snapshot,
            "write_policy": write_policy,
            "write_scope": scope,
            "diagnostics": diagnostics,
        }

    def collect_workspace_evidence(
        self,
        *,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
    ) -> dict[str, Any]:
        local_files = self.filter_workspace_bookkeeping_files(
            handle,
            git_manager._workspace_local_changes(),  # noqa: SLF001
        )
        write_policy = self.node_write_policy(handle, node)
        scope = self.classify_write_scope(
            changed_files=local_files,
            write_policy=write_policy,
            plan_path=handle.run.plan_path,
        )
        diagnostics = {
            "worktree_path": str(self.workspace_root),
            "worktree_exists": True,
            "changed_files": list(scope.get("changed_files", [])),
            "in_scope_files": list(scope.get("in_scope_files", [])),
            "out_of_scope_files": list(scope.get("out_of_scope_files", [])),
            "forbidden_files": list(scope.get("forbidden_files", [])),
            "plan_edit_files": list(scope.get("plan_edit_files", [])),
            "allowed_write_roots": list(scope.get("allowed_write_roots", [])),
            "forbidden_write_roots": list(scope.get("forbidden_write_roots", [])),
            "observed_out_of_scope_mutation": bool(
                scope.get("observed_out_of_scope_mutation")
            ),
        }
        return {
            "write_policy": write_policy,
            "write_scope": scope,
            "diagnostics": diagnostics,
        }

    def writeback_target(self, source: Path, plan: Any) -> tuple[str, Path | None]:
        mode = str(self.config.task_writeback_mode or "revision_only")
        if mode == "disabled":
            return mode, None
        if mode == "in_place":
            return mode, source
        filename = versioned_filename(plan.plan_id, "completed")
        return "revision_only", source.parent.parent / "plans" / filename

    def integration_overlap_ignore_paths(self, handle: "RuntimeHandle") -> list[str]:
        ignored: list[str] = []
        if str(handle.run.plan_path or "").strip():
            ignored.append(str(handle.run.plan_path))
        plan_path = Path(handle.run.plan_path)
        _mode, writeback_target = self.writeback_target(plan_path, handle.plan)
        if writeback_target is not None:
            ignored.append(str(writeback_target))
        return ignored

    def workspace_bookkeeping_paths(self, handle: "RuntimeHandle") -> set[str]:
        ignored = {
            str(item).replace("\\", "/")
            for item in self.integration_overlap_ignore_paths(handle)
            if str(item).strip()
        }
        ignored.add(".ralphite")
        return ignored

    def filter_workspace_bookkeeping_files(
        self, handle: "RuntimeHandle", files: list[str]
    ) -> list[str]:
        ignored = self.workspace_bookkeeping_paths(handle)
        filtered: list[str] = []
        for raw in files:
            normalized = str(raw).strip().replace("\\", "/")
            while normalized.startswith("./"):
                normalized = normalized[2:]
            if not normalized:
                continue
            if any(
                normalized == candidate or normalized.startswith(f"{candidate}/")
                for candidate in ignored
                if candidate
            ):
                continue
            filtered.append(normalized)
        return sorted(dict.fromkeys(filtered))

    def should_attempt_backend_failure_salvage(
        self, result: dict[str, Any], evidence: dict[str, Any]
    ) -> bool:
        reason = str(result.get("reason") or "")
        if reason not in {
            "backend_nonzero",
            "backend_payload_missing",
            "backend_payload_malformed",
            "backend_output_malformed",
        }:
            return False
        write_scope = (
            evidence.get("write_scope")
            if isinstance(evidence.get("write_scope"), dict)
            else {}
        )
        if bool(write_scope.get("observed_out_of_scope_mutation")):
            return False
        changed_files = (
            write_scope.get("changed_files")
            if isinstance(write_scope.get("changed_files"), list)
            else []
        )
        return bool(changed_files)

    def attempt_backend_failure_salvage(
        self,
        *,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
        result: dict[str, Any],
        evidence: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        commit_ok, commit_meta = git_manager.commit_worker(
            handle.run.metadata.setdefault("git_state", {}),
            node.phase,
            node.id,
            f"salvage({node.source_task_id or node.id}): promote backend output",
        )
        if not commit_ok:
            return False, commit_meta
        evaluate_acceptance = (
            self.evaluate_acceptance_callback or self.evaluate_acceptance_impl
        )
        acceptance_ok, acceptance_result = evaluate_acceptance(
            node,
            commit_meta,
            timeout_seconds=int(handle.plan.constraints.acceptance_timeout_seconds),
        )
        if not acceptance_ok:
            return False, acceptance_result
        diagnostics = (
            result.get("diagnostics")
            if isinstance(result.get("diagnostics"), dict)
            else {}
        )
        return True, {
            "mode": "backend_failure_salvaged",
            "backend_failure_reason": str(result.get("reason") or ""),
            "backend_failure": {
                "reason": str(result.get("reason") or ""),
                "error": str(result.get("error") or ""),
                "exit_code": result.get("exit_code"),
                "stdout_excerpt": str(result.get("stdout_excerpt") or ""),
                "stderr_excerpt": str(result.get("stderr_excerpt") or ""),
            },
            "summary": str(
                result.get("summary")
                or "salvaged worker output from local worktree evidence"
            ),
            "diagnostics": {
                **diagnostics,
                **(
                    evidence.get("diagnostics")
                    if isinstance(evidence.get("diagnostics"), dict)
                    else {}
                ),
                "salvaged_from_backend_failure": True,
            },
            "worker_evidence": (
                evidence.get("diagnostics")
                if isinstance(evidence.get("diagnostics"), dict)
                else {}
            ),
            "worktree": commit_meta,
            "acceptance": acceptance_result,
        }

    def should_attempt_orchestrator_backend_failure_salvage(
        self,
        result: dict[str, Any],
        *,
        git_manager: GitWorktreeManager,
        phase_branch: str,
        integration_worktree: str,
    ) -> bool:
        reason = str(result.get("reason") or "")
        if reason not in {
            "backend_nonzero",
            "backend_payload_missing",
            "backend_payload_malformed",
            "backend_output_malformed",
        }:
            return False
        snapshot = git_manager.inspect_managed_target(
            worktree_path=integration_worktree or None,
            branch=phase_branch or None,
        )
        if str(snapshot.get("status_porcelain") or "").strip():
            return True
        if phase_branch and git_manager._phase_touched_files(phase_branch):  # noqa: SLF001
            return True
        return False

    def attempt_orchestrator_backend_failure_salvage(
        self,
        *,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
        result: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        recovery = handle.run.metadata.setdefault("recovery", {})
        selected_mode = str(recovery.get("selected_mode") or "manual")
        selected_prompt = recovery.get("prompt")
        commit_ok, commit_meta = git_manager.commit_phase_integration_changes(
            handle.run.metadata.setdefault("git_state", {}),
            node.phase,
            f"orchestrator({node.phase}): prepare merged phase output",
        )
        if not commit_ok:
            return False, commit_meta
        status, merge_meta = git_manager.integrate_phase(
            handle.run.metadata.setdefault("git_state", {}),
            node.phase,
            recovery_mode=selected_mode,
            recovery_prompt=str(selected_prompt) if selected_prompt else None,
            ignore_overlap_paths=self.integration_overlap_ignore_paths(handle),
        )
        if status != "success":
            return False, merge_meta
        diagnostics = (
            result.get("diagnostics")
            if isinstance(result.get("diagnostics"), dict)
            else {}
        )
        return True, {
            "mode": "backend_failure_salvaged",
            "backend_failure_reason": str(result.get("reason") or ""),
            "backend_failure": {
                "reason": str(result.get("reason") or ""),
                "error": str(result.get("error") or ""),
                "exit_code": result.get("exit_code"),
                "stdout_excerpt": str(result.get("stdout_excerpt") or ""),
                "stderr_excerpt": str(result.get("stderr_excerpt") or ""),
            },
            "summary": str(
                result.get("summary")
                or "salvaged orchestrator output from local integration worktree evidence"
            ),
            "diagnostics": {
                **diagnostics,
                "salvaged_from_backend_failure": True,
            },
            "phase_commit": commit_meta,
            "integration": merge_meta,
        }

    def should_attempt_workspace_backend_failure_salvage(
        self,
        result: dict[str, Any],
        *,
        evidence: dict[str, Any],
        preexisting_dirty_files: list[str],
    ) -> bool:
        reason = str(result.get("reason") or "")
        if reason not in {
            "backend_nonzero",
            "backend_payload_missing",
            "backend_payload_malformed",
            "backend_output_malformed",
        }:
            return False
        if preexisting_dirty_files:
            return False
        write_scope = (
            evidence.get("write_scope")
            if isinstance(evidence.get("write_scope"), dict)
            else {}
        )
        if bool(write_scope.get("observed_out_of_scope_mutation")):
            return False
        changed_files = (
            write_scope.get("changed_files")
            if isinstance(write_scope.get("changed_files"), list)
            else []
        )
        return bool(changed_files)

    def attempt_workspace_backend_failure_salvage(
        self,
        *,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
        result: dict[str, Any],
        evidence: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        changed_files = (
            evidence.get("write_scope", {}).get("changed_files", [])
            if isinstance(evidence.get("write_scope"), dict)
            else []
        )
        commit_ok, commit_meta = git_manager.commit_workspace_changes(
            f"orchestrator({node.phase}): salvage backend output",
            paths=list(changed_files),
        )
        if not commit_ok:
            return False, commit_meta
        diagnostics = (
            result.get("diagnostics")
            if isinstance(result.get("diagnostics"), dict)
            else {}
        )
        return True, {
            "mode": "backend_failure_salvaged",
            "backend_failure_reason": str(result.get("reason") or ""),
            "backend_failure": {
                "reason": str(result.get("reason") or ""),
                "error": str(result.get("error") or ""),
                "exit_code": result.get("exit_code"),
                "stdout_excerpt": str(result.get("stdout_excerpt") or ""),
                "stderr_excerpt": str(result.get("stderr_excerpt") or ""),
            },
            "summary": str(
                result.get("summary")
                or "salvaged orchestrator output from local workspace evidence"
            ),
            "diagnostics": {
                **diagnostics,
                **(
                    evidence.get("diagnostics")
                    if isinstance(evidence.get("diagnostics"), dict)
                    else {}
                ),
                "salvaged_from_backend_failure": True,
            },
            "workspace_commit": commit_meta,
        }

    def resolve_worker_worktree(self, commit_meta: dict[str, Any]) -> Path:
        return self.git_orchestrator.resolve_worker_worktree(commit_meta)

    def is_worktree_relative_glob(self, path_glob: str) -> bool:
        return self.git_orchestrator.is_worktree_relative_glob(path_glob)

    def acceptance_command_argv(self, command: str) -> list[str]:
        text = str(command or "").strip()
        if not text:
            return []
        return shlex.split(text, posix=os.name != "nt")

    def expand_acceptance_command_globs(
        self, argv: list[str], *, worktree: Path
    ) -> list[str]:
        if not argv:
            return []
        expanded: list[str] = [argv[0]]
        for token in argv[1:]:
            text = str(token or "").strip()
            if (
                not text
                or text.startswith("-")
                or not glob.has_magic(text)
                or not self.is_worktree_relative_glob(text)
            ):
                expanded.append(token)
                continue
            matches = glob.glob(str(worktree / text), recursive=True)
            safe_matches: list[str] = []
            for match in matches:
                resolved = Path(match).resolve()
                try:
                    relative = resolved.relative_to(worktree)
                except ValueError:
                    continue
                if resolved.exists():
                    safe_matches.append(str(relative).replace("\\", "/"))
            if safe_matches:
                expanded.extend(sorted(dict.fromkeys(safe_matches)))
            else:
                expanded.append(token)
        return expanded

    def evaluate_acceptance_impl(
        self,
        node: RuntimeNodeSpec,
        commit_meta: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> tuple[bool, dict[str, Any]]:
        acceptance = node.acceptance if isinstance(node.acceptance, dict) else {}
        commands = (
            acceptance.get("commands")
            if isinstance(acceptance.get("commands"), list)
            else []
        )
        required_artifacts = (
            acceptance.get("required_artifacts")
            if isinstance(acceptance.get("required_artifacts"), list)
            else []
        )
        rubric = (
            acceptance.get("rubric")
            if isinstance(acceptance.get("rubric"), list)
            else []
        )
        if not commands and not required_artifacts:
            return True, {"commands": [], "required_artifacts": [], "rubric": rubric}

        worktree = self.resolve_worker_worktree(commit_meta)
        runner_env = self.build_worker_env(worktree=worktree)
        command_results: list[dict[str, Any]] = []
        for command in commands:
            if not isinstance(command, str) or not command.strip():
                continue
            argv = self.acceptance_command_argv(command)
            if not argv:
                continue
            argv = self.expand_acceptance_command_globs(argv, worktree=worktree)
            try:
                run = subprocess.run(
                    argv,
                    cwd=worktree,
                    env=runner_env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout_seconds)),
                )
            except subprocess.TimeoutExpired as exc:
                return (
                    False,
                    {
                        "reason": "acceptance_command_timeout",
                        "worktree": str(worktree),
                        "failed_command": command,
                        "timeout_seconds": int(timeout_seconds),
                        "stdout": exc.stdout or "",
                        "stderr": exc.stderr or "",
                        "commands": command_results,
                        "required_artifacts": [],
                        "rubric": rubric,
                    },
                )
            result = {
                "command": command,
                "exit_code": run.returncode,
                "stdout": run.stdout,
                "stderr": run.stderr,
            }
            command_results.append(result)
            if run.returncode != 0:
                return (
                    False,
                    {
                        "reason": "acceptance_command_failed",
                        "worktree": str(worktree),
                        "failed_command": command,
                        "commands": command_results,
                        "required_artifacts": [],
                        "rubric": rubric,
                    },
                )

        artifact_results: list[dict[str, Any]] = []
        for item in required_artifacts:
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("id") or "artifact")
            path_glob = str(item.get("path_glob") or "").strip()
            fmt = str(item.get("format") or "unknown")
            if path_glob and not self.is_worktree_relative_glob(path_glob):
                return (
                    False,
                    {
                        "reason": "acceptance_artifact_out_of_bounds",
                        "worktree": str(worktree),
                        "artifact": artifact_id,
                        "path_glob": path_glob,
                        "commands": command_results,
                        "required_artifacts": artifact_results,
                        "rubric": rubric,
                    },
                )

            raw_matches = (
                glob.glob(str(worktree / path_glob), recursive=True)
                if path_glob
                else []
            )
            matches: list[str] = []
            for path in raw_matches:
                resolved = Path(path).resolve()
                try:
                    resolved.relative_to(worktree)
                except ValueError:
                    return (
                        False,
                        {
                            "reason": "acceptance_artifact_out_of_bounds",
                            "worktree": str(worktree),
                            "artifact": artifact_id,
                            "path_glob": path_glob,
                            "out_of_bounds_path": str(resolved),
                            "commands": command_results,
                            "required_artifacts": artifact_results,
                            "rubric": rubric,
                        },
                    )
                matches.append(str(resolved))
            artifact_results.append(
                {
                    "id": artifact_id,
                    "format": fmt,
                    "path_glob": path_glob,
                    "matches": matches,
                }
            )
            if not matches:
                return (
                    False,
                    {
                        "reason": "acceptance_artifact_missing",
                        "worktree": str(worktree),
                        "missing_artifact": artifact_id,
                        "commands": command_results,
                        "required_artifacts": artifact_results,
                        "rubric": rubric,
                    },
                )

        return (
            True,
            {
                "commands": command_results,
                "required_artifacts": artifact_results,
                "rubric": rubric,
            },
        )

    def run_node_impl(
        self,
        handle: "RuntimeHandle",
        node: RuntimeNodeSpec,
        git_manager: GitWorktreeManager,
    ) -> tuple[str, dict[str, Any]]:
        profile = handle.profile_map.get(node.agent_profile_id)
        if not profile:
            return "failure", {
                "reason": "runtime_error",
                "error": f"unknown agent_id {node.agent_profile_id}",
            }

        if node.role == "orchestrator":
            agent_worktree = self.workspace_root
            prep_meta: dict[str, Any] = {}
            preexisting_workspace_dirty: list[str] = []
            if node.behavior_kind == BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION.value:
                prep_status, prep_meta = git_manager.prepare_phase_integration(
                    handle.run.metadata.setdefault("git_state", {}),
                    node.phase,
                )
                if prep_status == "recovery_required":
                    return "recovery_required", prep_meta
                if prep_status == "failed":
                    return "failure", {
                        "reason": "runtime_error",
                        **prep_meta,
                        "preserve_targets": [
                            {
                                "scope": "phase",
                                "phase": node.phase,
                                "node_id": node.id,
                                "worktree_path": str(prep_meta.get("worktree") or ""),
                                "branch": str(prep_meta.get("phase_branch") or ""),
                                "committed": None,
                            }
                        ],
                    }
                integration_worktree = (
                    Path(str(prep_meta.get("worktree") or self.workspace_root))
                    .expanduser()
                    .resolve()
                )
                if integration_worktree.exists():
                    agent_worktree = integration_worktree
            else:
                preexisting_workspace_dirty = self.filter_workspace_bookkeeping_files(
                    handle,
                    git_manager._workspace_local_changes(),  # noqa: SLF001
                )

            execute_agent = self.execute_agent_callback or self.execute_agent_impl
            agent_ok, agent_result = execute_agent(
                handle,
                node,
                profile,
                handle.permission_snapshot,
                worktree=agent_worktree,
            )
            if not agent_ok:
                if (
                    node.behavior_kind
                    == BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION.value
                    and self.should_attempt_orchestrator_backend_failure_salvage(
                        agent_result,
                        git_manager=git_manager,
                        phase_branch=str(prep_meta.get("phase_branch") or ""),
                        integration_worktree=str(prep_meta.get("worktree") or ""),
                    )
                ):
                    salvaged, salvage_result = (
                        self.attempt_orchestrator_backend_failure_salvage(
                            handle=handle,
                            node=node,
                            git_manager=git_manager,
                            result=agent_result,
                        )
                    )
                    if salvaged:
                        return "success", salvage_result
                if (
                    node.behavior_kind
                    != BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION.value
                ):
                    evidence = self.collect_workspace_evidence(
                        handle=handle,
                        node=node,
                        git_manager=git_manager,
                    )
                    if self.should_attempt_workspace_backend_failure_salvage(
                        agent_result,
                        evidence=evidence,
                        preexisting_dirty_files=preexisting_workspace_dirty,
                    ):
                        salvaged, salvage_result = (
                            self.attempt_workspace_backend_failure_salvage(
                                handle=handle,
                                node=node,
                                git_manager=git_manager,
                                result=agent_result,
                                evidence=evidence,
                            )
                        )
                        if salvaged:
                            return "success", salvage_result
                return "failure", agent_result

            if node.behavior_kind == BehaviorKind.MERGE_AND_CONFLICT_RESOLUTION.value:
                recovery = handle.run.metadata.setdefault("recovery", {})
                selected_mode = str(recovery.get("selected_mode") or "manual")
                selected_prompt = recovery.get("prompt")
                commit_ok, commit_meta = git_manager.commit_phase_integration_changes(
                    handle.run.metadata.setdefault("git_state", {}),
                    node.phase,
                    f"orchestrator({node.phase}): prepare merged phase output",
                )
                if not commit_ok:
                    if str(commit_meta.get("reason") or "") in {
                        "worker_merge_conflict",
                        "simulated_conflict",
                    }:
                        return "recovery_required", commit_meta
                    if str(commit_meta.get("reason") or "") == (
                        "base_integration_blocked_by_local_changes"
                    ):
                        return "recovery_required", commit_meta
                    return "failure", {
                        "reason": "runtime_error",
                        **commit_meta,
                        "preserve_targets": [
                            {
                                "scope": "phase",
                                "phase": node.phase,
                                "node_id": node.id,
                                "worktree_path": str(commit_meta.get("worktree") or ""),
                                "branch": str(commit_meta.get("phase_branch") or ""),
                                "committed": bool(
                                    str(commit_meta.get("mode") or "") == "committed"
                                ),
                            }
                        ],
                    }

                status, merge_meta = git_manager.integrate_phase(
                    handle.run.metadata.setdefault("git_state", {}),
                    node.phase,
                    recovery_mode=selected_mode,
                    recovery_prompt=str(selected_prompt) if selected_prompt else None,
                    ignore_overlap_paths=self.integration_overlap_ignore_paths(handle),
                )
                if status == "recovery_required":
                    return "recovery_required", merge_meta
                if status == "failed":
                    return "failure", {
                        "reason": "runtime_error",
                        **merge_meta,
                        "preserve_targets": [
                            {
                                "scope": "phase",
                                "phase": node.phase,
                                "node_id": node.id,
                                "worktree_path": str(prep_meta.get("worktree") or ""),
                                "branch": str(prep_meta.get("phase_branch") or ""),
                                "committed": bool(
                                    str(commit_meta.get("mode") or "") == "committed"
                                ),
                            }
                        ],
                    }
                return "success", {
                    **agent_result,
                    "phase_commit": commit_meta,
                    "integration": merge_meta,
                }

            return "success", agent_result

        if node.role == "worker":
            git_manager.prepare_phase(
                handle.run.metadata.setdefault("git_state", {}), node.phase
            )
            worker_info = git_manager.prepare_worker(
                handle.run.metadata.setdefault("git_state", {}),
                node.phase,
                node.id,
            )
            if worker_info.get("prepare_error"):
                return "failure", {
                    "reason": "worktree_prepare_failed",
                    "error": worker_info["prepare_error"],
                }
            worker_worktree_candidate = (
                Path(str(worker_info.get("worktree_path") or self.workspace_root))
                .expanduser()
                .resolve()
            )
            worker_worktree = (
                worker_worktree_candidate
                if worker_worktree_candidate.exists()
                else self.workspace_root
            )
            execute_agent = self.execute_agent_callback or self.execute_agent_impl
            ok, result = execute_agent(
                handle,
                node,
                profile,
                handle.permission_snapshot,
                worktree=worker_worktree,
            )
            if not ok:
                evidence = self.collect_worker_evidence(
                    handle=handle,
                    node=node,
                    git_manager=git_manager,
                    worktree_path=str(worker_info.get("worktree_path") or ""),
                )
                if self.should_attempt_backend_failure_salvage(result, evidence):
                    salvaged, salvage_result = self.attempt_backend_failure_salvage(
                        handle=handle,
                        node=node,
                        git_manager=git_manager,
                        result=result,
                        evidence=evidence,
                    )
                    if salvaged:
                        return "success", salvage_result
                result = {
                    **result,
                    "worker_evidence": evidence.get("diagnostics", {}),
                    "preserve_targets": [
                        {
                            "scope": "worker",
                            "phase": node.phase,
                            "node_id": node.id,
                            "worktree_path": str(
                                worker_info.get("worktree_path") or ""
                            ),
                            "branch": str(worker_info.get("branch") or ""),
                            "committed": False,
                        }
                    ],
                }
                return "failure", result

            evidence = self.collect_worker_evidence(
                handle=handle,
                node=node,
                git_manager=git_manager,
                worktree_path=str(worker_info.get("worktree_path") or ""),
            )
            if bool(evidence["write_scope"].get("observed_out_of_scope_mutation")):
                diagnostics = (
                    result.get("diagnostics")
                    if isinstance(result.get("diagnostics"), dict)
                    else {}
                )
                return "failure", {
                    "reason": "backend_out_of_worktree_mutation",
                    "error": "observed file mutations exceeded the assigned write scope",
                    "diagnostics": {
                        **diagnostics,
                        **evidence["diagnostics"],
                    },
                    "preserve_targets": [
                        {
                            "scope": "worker",
                            "phase": node.phase,
                            "node_id": node.id,
                            "worktree_path": str(
                                worker_info.get("worktree_path") or ""
                            ),
                            "branch": str(worker_info.get("branch") or ""),
                            "committed": False,
                        }
                    ],
                }

            commit_ok, commit_meta = git_manager.commit_worker(
                handle.run.metadata.setdefault("git_state", {}),
                node.phase,
                node.id,
                f"task({node.source_task_id or node.id}): {node.task[:72]}",
            )
            if not commit_ok:
                commit_meta = {
                    **commit_meta,
                    "preserve_targets": [
                        {
                            "scope": "worker",
                            "phase": node.phase,
                            "node_id": node.id,
                            "worktree_path": str(
                                worker_info.get("worktree_path") or ""
                            ),
                            "branch": str(worker_info.get("branch") or ""),
                            "committed": bool(worker_info.get("committed")),
                        }
                    ],
                }
                return "failure", commit_meta
            evaluate_acceptance = (
                self.evaluate_acceptance_callback or self.evaluate_acceptance_impl
            )
            acceptance_ok, acceptance_result = evaluate_acceptance(
                node,
                commit_meta,
                timeout_seconds=int(handle.plan.constraints.acceptance_timeout_seconds),
            )
            if not acceptance_ok:
                acceptance_result = {
                    **acceptance_result,
                    "preserve_targets": [
                        {
                            "scope": "worker",
                            "phase": node.phase,
                            "node_id": node.id,
                            "worktree_path": str(commit_meta.get("worktree") or ""),
                            "branch": str(commit_meta.get("branch") or ""),
                            "committed": True,
                        }
                    ],
                }
                return "failure", acceptance_result
            return "success", {
                **result,
                "worker_evidence": evidence.get("diagnostics", {}),
                "worktree": commit_meta,
                "acceptance": acceptance_result,
            }

        execute_agent = self.execute_agent_callback or self.execute_agent_impl
        ok, result = execute_agent(
            handle,
            node,
            profile,
            handle.permission_snapshot,
            worktree=self.workspace_root,
        )
        return ("success", result) if ok else ("failure", result)
