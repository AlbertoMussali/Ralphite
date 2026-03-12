from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from pathlib import PureWindowsPath
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any

from ralphite.engine.process_guard import (
    clear_managed_process_marker,
    terminate_process_tree,
    write_managed_process_marker,
)
from ralphite.engine.structure_compiler import RuntimeNodeSpec
from ralphite.schemas.prompt_templates import (
    ORCHESTRATOR_PLACEHOLDER_TOKENS,
    WORKER_PLACEHOLDER_TOKENS,
    render_prompt_template,
)


@dataclass(frozen=True)
class BackendExecutionConfig:
    backend: str
    model: str
    reasoning_effort: str
    cursor_command: str
    timeout_seconds: int = 900


def build_worker_subprocess_env(*, worktree: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    if os.name != "nt":
        return env

    worktree_key = hashlib.sha1(
        str(worktree.expanduser().resolve()).encode("utf-8")
    ).hexdigest()[:12]
    cache_root = os.path.join(tempfile.gettempdir(), "ralphite-uv-cache", worktree_key)
    temp_root = os.path.join(cache_root, "tmp")
    venv_root = os.path.join(cache_root, "venv")
    os.makedirs(cache_root, exist_ok=True)
    os.makedirs(temp_root, exist_ok=True)
    os.makedirs(venv_root, exist_ok=True)
    env["UV_CACHE_DIR"] = cache_root
    env["UV_PROJECT_ENVIRONMENT"] = venv_root
    env["TMP"] = temp_root
    env["TEMP"] = temp_root
    return env


def normalize_backend_name(raw_backend: str | None) -> str:
    backend = (raw_backend or "codex").strip().lower()
    if backend not in {"codex", "cursor"}:
        return "codex"
    return backend


def _normalize_reasoning_effort(raw_reasoning_effort: str | None) -> str:
    reasoning_effort = (raw_reasoning_effort or "medium").strip().lower()
    if reasoning_effort not in {"low", "medium", "high"}:
        return "medium"
    return reasoning_effort


def build_codex_exec_command(
    *,
    prompt: str,
    model: str,
    reasoning_effort: str,
    worktree: Path | None = None,
    sandbox: str = "workspace-write",
) -> list[str]:
    command = [
        *_resolve_codex_command_prefix(),
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
    ]
    if worktree is not None:
        command.extend(["--cd", str(worktree)])
    command.extend(
        [
            "--model",
            model.strip() or "gpt-5.3-codex",
            "-c",
            f'model_reasoning_effort="{_normalize_reasoning_effort(reasoning_effort)}"',
            "-c",
            'approval_policy="never"',
            "--sandbox",
            sandbox.strip() or "workspace-write",
            prompt,
        ]
    )
    return command


def build_cursor_exec_command(
    *,
    prompt: str,
    model: str,
    cursor_command: str,
    force: bool = True,
) -> list[str]:
    command = [*_resolve_cursor_command_prefix(cursor_command), "-p"]
    if force:
        command.append("--force")
    command.extend(
        [
            "--output-format",
            "json",
            "--model",
            model.strip() or "gpt-5.3-codex",
            prompt,
        ]
    )
    return command


def _split_command_words(raw_command: str, *, default_program: str) -> list[str]:
    cleaned = (raw_command or "").strip() or default_program
    stripped = _strip_wrapping_quotes(cleaned)
    if _is_windows_absolute_path(stripped):
        return [stripped]
    try:
        parts = shlex.split(cleaned, posix=os.name != "nt")
    except ValueError:
        parts = [cleaned]
    return parts or [default_program]


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _is_windows_absolute_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", value)) or value.startswith("\\\\")


def _candidate_command_path(raw_program: str) -> str | Path:
    program = _strip_wrapping_quotes(raw_program)
    if _is_windows_absolute_path(program):
        return program
    candidate = Path(program).expanduser()
    if candidate.is_absolute():
        return candidate
    return (Path.cwd() / candidate).resolve()


def _default_cursor_script_candidates() -> list[Path]:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if not local_appdata:
        return []
    return [Path(local_appdata) / "cursor-agent" / "agent.ps1"]


def _resolve_path_launcher(program: str) -> str | None:
    program = _strip_wrapping_quotes(program)
    resolved = shutil.which(program)
    if resolved:
        return resolved
    if Path(program).suffix:
        return None
    for suffix in (".cmd", ".exe", ".bat", ".ps1"):
        resolved = shutil.which(f"{program}{suffix}")
        if resolved:
            return resolved
    return None


def _resolve_command_launcher(
    raw_command: str,
    *,
    default_program: str,
    default_script_candidates: list[Path] | None = None,
) -> tuple[str, str | Path, list[str]]:
    parts = _split_command_words(raw_command, default_program=default_program)
    program = _strip_wrapping_quotes(parts[0])
    remainder = parts[1:]
    if program.lower().endswith(".ps1"):
        return "script", _candidate_command_path(program), remainder

    script_candidates = default_script_candidates or []
    if program.lower() in {default_program.lower(), f"{default_program.lower()}.ps1"}:
        for candidate in script_candidates:
            if candidate.exists():
                return "script", candidate.resolve(), remainder
    resolved = _resolve_path_launcher(program)
    if resolved:
        if resolved.lower().endswith(".ps1"):
            return "script", _candidate_command_path(resolved), remainder
        return "direct", program, remainder
    return "direct", program, remainder


def _resolve_command_prefix(
    raw_command: str,
    *,
    default_program: str,
    default_script_candidates: list[Path] | None = None,
) -> list[str]:
    launch_kind, target, remainder = _resolve_command_launcher(
        raw_command,
        default_program=default_program,
        default_script_candidates=default_script_candidates,
    )
    if launch_kind == "script":
        host = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        script_path = str(target)
        return [
            host,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
            *remainder,
        ]
    return [str(target), *remainder]


def _probe_command(
    raw_command: str,
    *,
    default_program: str,
    default_script_candidates: list[Path] | None = None,
) -> tuple[bool, str]:
    launch_kind, target, _remainder = _resolve_command_launcher(
        raw_command,
        default_program=default_program,
        default_script_candidates=default_script_candidates,
    )
    if launch_kind == "script":
        script_path = str(target)
        if not _is_windows_absolute_path(script_path):
            resolved_script = Path(script_path).expanduser().resolve()
            if not resolved_script.exists():
                return False, f"PowerShell script not found: {resolved_script}"
            script_path = str(resolved_script)
        host = shutil.which("pwsh") or shutil.which("powershell")
        if not host:
            return False, f"PowerShell host not found for script: {script_path}"
        return True, f"{host} -> {script_path}"

    program = str(target)
    resolved = shutil.which(program)
    if resolved:
        return True, resolved
    candidate = _candidate_command_path(program)
    if candidate.exists():
        return True, str(candidate)
    return False, "not in PATH"


def _resolve_codex_command_prefix() -> list[str]:
    return _resolve_command_prefix("codex", default_program="codex")


def _resolve_cursor_command_prefix(cursor_command: str) -> list[str]:
    return _resolve_command_prefix(
        cursor_command,
        default_program="agent",
        default_script_candidates=_default_cursor_script_candidates(),
    )


def probe_codex_command() -> tuple[bool, str]:
    return _probe_command("codex", default_program="codex")


def probe_cursor_command(cursor_command: str) -> tuple[bool, str]:
    return _probe_command(
        cursor_command,
        default_program="agent",
        default_script_candidates=_default_cursor_script_candidates(),
    )


def _summarize_acceptance(node: RuntimeNodeSpec) -> str:
    acceptance = node.acceptance if isinstance(node.acceptance, dict) else {}
    commands = (
        acceptance.get("commands")
        if isinstance(acceptance.get("commands"), list)
        else []
    )
    artifacts = (
        acceptance.get("required_artifacts")
        if isinstance(acceptance.get("required_artifacts"), list)
        else []
    )
    rubric = (
        acceptance.get("rubric") if isinstance(acceptance.get("rubric"), list) else []
    )
    parts = [
        f"acceptance.commands={len(commands)}",
        f"acceptance.required_artifacts={len(artifacts)}",
        f"acceptance.rubric={len(rubric)}",
    ]
    return ", ".join(parts)


def build_node_prompt(
    node: RuntimeNodeSpec,
    *,
    worktree: Path,
    permission_snapshot: dict[str, list[str]],
    plan_id: str,
    plan_name: str,
    agent_id: str,
    agent_role: str,
    system_prompt: str | None = None,
    behavior_prompt_template: str | None = None,
    write_policy: dict[str, Any] | None = None,
) -> str:
    role_name = "worker" if node.role == "worker" else "orchestrator"
    acceptance_summary = _summarize_acceptance(node)
    render_context = {
        "plan_id": str(plan_id),
        "plan_name": str(plan_name),
        "agent_id": str(agent_id),
        "agent_role": str(agent_role),
        "node_id": str(node.id),
        "task_id": str(node.source_task_id or "-"),
        "task_title": str(node.task or ""),
        "phase": str(node.phase or ""),
        "lane": str(node.lane or ""),
        "cell_id": str(node.cell_id or ""),
        "worktree": str(worktree),
        "acceptance_summary": acceptance_summary,
        "behavior_id": str(node.behavior_id or "-"),
        "behavior_kind": str(node.behavior_kind or "-"),
    }
    prompt_tokens = (
        WORKER_PLACEHOLDER_TOKENS
        if node.role == "worker"
        else ORCHESTRATOR_PLACEHOLDER_TOKENS
    )
    rendered_system_prompt = render_prompt_template(
        system_prompt or "",
        context=render_context,
        allowed_tokens=prompt_tokens,
    ).strip()
    rendered_behavior_prompt = ""
    if node.role == "orchestrator":
        rendered_behavior_prompt = render_prompt_template(
            behavior_prompt_template or "",
            context=render_context,
            allowed_tokens=ORCHESTRATOR_PLACEHOLDER_TOKENS,
        ).strip()

    base = [
        f"You are executing a Ralphite {role_name} node.",
        f"Plan id: {plan_id}",
        f"Plan name: {plan_name}",
        f"Agent id: {agent_id}",
        f"Agent role: {agent_role}",
        f"Node id: {node.id}",
        f"Task id: {node.source_task_id or '-'}",
        f"Task title: {node.task}",
        f"Phase: {node.phase}",
        f"Lane: {node.lane}",
        f"Cell: {node.cell_id}",
        f"Worktree: {worktree}",
        "",
        "Hard constraints:",
        f"- Operate ONLY within this worktree path: {worktree}",
        "- Do not read/write files outside this worktree.",
        "- Keep changes minimal and directly related to the task.",
        "- Return a concise completion summary and important file changes.",
        "",
        "Permission snapshot:",
        f"- allow_tools={permission_snapshot.get('allow_tools', [])}",
        f"- deny_tools={permission_snapshot.get('deny_tools', [])}",
        f"- allow_mcps={permission_snapshot.get('allow_mcps', [])}",
        f"- deny_mcps={permission_snapshot.get('deny_mcps', [])}",
    ]
    policy = write_policy if isinstance(write_policy, dict) else {}
    allowed_roots = [
        str(item).strip()
        for item in policy.get("allowed_write_roots", [])
        if str(item).strip()
    ]
    forbidden_roots = [
        str(item).strip()
        for item in policy.get("forbidden_write_roots", [])
        if str(item).strip()
    ]
    if policy:
        base.extend(
            [
                "",
                "Machine-enforced write policy:",
                f"- allowed_write_roots={allowed_roots}",
                f"- forbidden_write_roots={forbidden_roots}",
                f"- allow_plan_edits={bool(policy.get('allow_plan_edits'))}",
                f"- allow_root_writes={bool(policy.get('allow_root_writes'))}",
            ]
        )
    if rendered_system_prompt:
        base.extend(
            [
                "",
                "Role system prompt:",
                rendered_system_prompt,
            ]
        )
    if node.role == "worker":
        base.extend(
            [
                "",
                "Worker requirements:",
                f"- Acceptance summary: {acceptance_summary}",
                "- Ensure implementation can pass acceptance commands and artifact checks.",
            ]
        )
    else:
        base.extend(
            [
                "",
                "Orchestrator requirements:",
                "- Focus on merge/conflict policy, handoff quality, and clear completion context.",
                "- Summarize any unresolved risks explicitly.",
            ]
        )
        if rendered_behavior_prompt:
            base.extend(
                [
                    "",
                    "Behavior prompt template:",
                    rendered_behavior_prompt,
                ]
            )
    return "\n".join(base)


def _command_fingerprint(args: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in args)


def _parse_codex_jsonl(stdout: str) -> tuple[str | None, str | None]:
    last_message: str | None = None
    error_message: str | None = None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        ptype = str(payload.get("type", ""))
        if ptype == "item.completed":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if str(item.get("type")) == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    last_message = text.strip()
            if str(item.get("type")) == "error":
                msg = item.get("message")
                if isinstance(msg, str) and msg.strip():
                    error_message = msg.strip()
        elif ptype == "error":
            msg = payload.get("message")
            if isinstance(msg, str) and msg.strip():
                error_message = msg.strip()
        elif ptype == "turn.failed":
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            msg = err.get("message")
            if isinstance(msg, str) and msg.strip():
                error_message = msg.strip()
    return last_message, error_message


def _parse_cursor_output(stdout: str) -> tuple[str | None, str | None]:
    if not stdout.strip():
        return None, "empty cursor output"
    # Most headless outputs are single JSON objects; fall back to the last JSON line.
    candidates = [
        stdout.strip(),
        *[line.strip() for line in stdout.splitlines() if line.strip().startswith("{")],
    ]
    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            for key in ("text", "message", "final", "output", "result"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), None
        if isinstance(payload, str) and payload.strip():
            return payload.strip(), None
    # Cursor may return plain text in some environments.
    return stdout.strip(), None


def _classify_backend_error(message: str) -> str:
    lowered = message.lower()
    if "not supported" in lowered and "model" in lowered:
        return "backend_model_unsupported"
    if "login" in lowered or "auth" in lowered or "unauthorized" in lowered:
        return "backend_auth_failed"
    if "not found" in lowered or "no such file" in lowered:
        return "backend_binary_missing"
    return "backend_nonzero"


def _mentions_external_path(summary: str, *, worktree: Path) -> bool:
    if not summary:
        return False
    text = summary.lower()
    if "outside worktree" in text or "outside workspace" in text:
        return True
    resolved_worktree = worktree.resolve()
    worktree_text = str(resolved_worktree)
    worktree_is_windows = bool(re.match(r"^[A-Za-z]:[\\/]", worktree_text))

    def _is_windows_abs(raw: str) -> bool:
        return bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith("\\\\")

    candidates = set(
        re.findall(r"[A-Za-z]:[\\/][^\"'\r\n]+", summary)
        + re.findall(r"\\\\[^\s\"']+[^\r\n]*", summary)
        + summary.split()
    )
    for token in candidates:
        candidate = token.strip(".,:;!\"'`()[]{}")
        if not candidate:
            continue
        if candidate.startswith("/"):
            path = Path(candidate)
            try:
                path.resolve().relative_to(resolved_worktree)
            except Exception:
                return True
            continue
        if _is_windows_abs(candidate):
            if not worktree_is_windows:
                return True
            try:
                PureWindowsPath(candidate).relative_to(PureWindowsPath(worktree_text))
            except Exception:
                return True
    return False


def execute_headless_agent(
    *,
    config: BackendExecutionConfig,
    prompt: str,
    worktree: Path,
) -> tuple[bool, dict[str, Any]]:
    backend = normalize_backend_name(config.backend)
    model = (config.model or "gpt-5.3-codex").strip() or "gpt-5.3-codex"
    reasoning_effort = _normalize_reasoning_effort(config.reasoning_effort)
    cwd = worktree.expanduser().resolve()
    if not cwd.exists():
        return False, {"reason": "backend_worktree_missing", "worktree": str(cwd)}

    if os.getenv("RALPHITE_DEV_SIMULATED_EXECUTION", "") == "1":
        time.sleep(float(os.getenv("RALPHITE_RUNNER_SIMULATED_TASK_SECONDS", "0.2")))
        return True, {
            "status": "success",
            "summary": f"[simulated] {prompt.splitlines()[2] if len(prompt.splitlines()) > 2 else 'Executed task'}",
            "backend": "simulated",
            "model": model,
            "reasoning_effort": reasoning_effort,
            "command_fingerprint": "simulated",
            "exit_code": 0,
            "duration_seconds": 0.2,
            "worktree": str(cwd),
            "assigned_worktree_root": str(cwd),
            "changed_files": [],
            "artifact_paths": [],
            "commit_sha": "",
            "stdout_excerpt": "",
            "stderr_excerpt": "",
            "backend_payload": {},
        }

    if backend == "cursor":
        cmd = build_cursor_exec_command(
            prompt=prompt,
            model=model,
            cursor_command=config.cursor_command,
            force=True,
        )
    else:
        backend = "codex"
        cmd = build_codex_exec_command(
            prompt=prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            worktree=cwd,
            sandbox="workspace-write",
        )

    started = time.perf_counter()
    runner_env = build_worker_subprocess_env(worktree=cwd)
    process: subprocess.Popen[str] | None = None
    stdout = ""
    stderr = ""
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": runner_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(cmd, **popen_kwargs)
        write_managed_process_marker(
            cwd,
            pid=int(process.pid),
            command=cmd[:-1],
            backend=backend,
        )
        stdout, stderr = process.communicate(
            timeout=max(1, int(config.timeout_seconds))
        )
    except FileNotFoundError as exc:
        return False, {
            "status": "failed",
            "reason": "backend_binary_missing",
            "backend": backend,
            "error": str(exc),
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "worktree": str(cwd),
            "assigned_worktree_root": str(cwd),
        }
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            terminate_process_tree(int(process.pid))
            stdout, stderr = process.communicate()
        else:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
        clear_managed_process_marker(cwd)
        return False, {
            "status": "failed",
            "reason": "backend_timeout",
            "backend": backend,
            "timeout_seconds": int(config.timeout_seconds),
            "stdout": stdout,
            "stderr": stderr,
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "worktree": str(cwd),
            "assigned_worktree_root": str(cwd),
            "backend_process_terminated": process is not None,
        }
    except Exception as exc:  # noqa: BLE001
        if process is not None:
            terminate_process_tree(int(process.pid))
            clear_managed_process_marker(cwd)
        return False, {
            "status": "failed",
            "reason": "backend_execution_error",
            "backend": backend,
            "error": str(exc),
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "worktree": str(cwd),
            "assigned_worktree_root": str(cwd),
        }
    finally:
        if process is not None and process.poll() is not None:
            clear_managed_process_marker(cwd)

    duration_seconds = round(max(0.0, time.perf_counter() - started), 3)
    stdout = stdout or ""
    stderr = stderr or ""

    if backend == "codex":
        text, parse_error = _parse_codex_jsonl(stdout)
    else:
        text, parse_error = _parse_cursor_output(stdout)

    returncode = int(process.returncode if process is not None else 1)
    if returncode != 0:
        detail = (parse_error or stderr or stdout).strip() or f"exit={returncode}"
        return False, {
            "status": "failed",
            "reason": _classify_backend_error(detail),
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "exit_code": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": detail,
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "duration_seconds": duration_seconds,
            "worktree": str(cwd),
            "assigned_worktree_root": str(cwd),
            "stdout_excerpt": stdout[-1000:],
            "stderr_excerpt": stderr[-1000:],
            "backend_payload": {},
        }

    if parse_error:
        reason = (
            "backend_payload_malformed"
            if backend == "cursor"
            else _classify_backend_error(parse_error)
        )
        return False, {
            "status": "failed",
            "reason": reason,
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "exit_code": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": parse_error,
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "duration_seconds": duration_seconds,
            "worktree": str(cwd),
            "assigned_worktree_root": str(cwd),
            "stdout_excerpt": stdout[-1000:],
            "stderr_excerpt": stderr[-1000:],
            "backend_payload": {},
            "diagnostics": {"payload_status": "malformed"},
        }
    if not text:
        return False, {
            "status": "failed",
            "reason": "backend_payload_missing",
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "exit_code": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": "no final agent message",
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "duration_seconds": duration_seconds,
            "worktree": str(cwd),
            "assigned_worktree_root": str(cwd),
            "stdout_excerpt": stdout[-1000:],
            "stderr_excerpt": stderr[-1000:],
            "backend_payload": {},
            "diagnostics": {"payload_status": "missing"},
        }
    external_path_mentioned = _mentions_external_path(text, worktree=cwd)
    return True, {
        "status": "success",
        "summary": text,
        "backend": backend,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "exit_code": returncode,
        "command_fingerprint": _command_fingerprint(cmd[:-1]),
        "duration_seconds": duration_seconds,
        "worktree": str(cwd),
        "assigned_worktree_root": str(cwd),
        "changed_files": [],
        "artifact_paths": [],
        "commit_sha": "",
        "stdout_excerpt": stdout[-1000:],
        "stderr_excerpt": stderr[-1000:],
        "backend_payload": {},
        "diagnostics": {
            "external_path_mentioned": external_path_mentioned,
        },
    }
