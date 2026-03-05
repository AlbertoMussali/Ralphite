from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any

from ralphite_engine.structure_compiler import RuntimeNodeSpec


@dataclass(frozen=True)
class BackendExecutionConfig:
    backend: str
    model: str
    reasoning_effort: str
    cursor_command: str
    timeout_seconds: int = 900


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
) -> str:
    role_name = "worker" if node.role == "worker" else "orchestrator"
    base = [
        f"You are executing a Ralphite {role_name} node.",
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
    if node.role == "worker":
        base.extend(
            [
                "",
                "Worker requirements:",
                f"- Acceptance summary: {_summarize_acceptance(node)}",
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
            for key in ("text", "message", "final", "output"):
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
    for token in summary.split():
        if not token.startswith("/"):
            continue
        candidate = token.strip(".,:;!\"'`()[]{}")
        if not candidate.startswith("/"):
            continue
        path = Path(candidate)
        try:
            path.resolve().relative_to(worktree.resolve())
        except Exception:
            return True
    return False


def execute_headless_agent(
    *,
    config: BackendExecutionConfig,
    prompt: str,
    worktree: Path,
) -> tuple[bool, dict[str, Any]]:
    backend = (config.backend or "codex").strip().lower()
    model = (config.model or "gpt-5.3-codex").strip() or "gpt-5.3-codex"
    reasoning_effort = (config.reasoning_effort or "medium").strip().lower() or "medium"
    cwd = worktree.expanduser().resolve()
    if not cwd.exists():
        return False, {"reason": "backend_worktree_missing", "worktree": str(cwd)}

    if os.getenv("RALPHITE_DEV_SIMULATED_EXECUTION", "") == "1":
        time.sleep(float(os.getenv("RALPHITE_RUNNER_SIMULATED_TASK_SECONDS", "0.2")))
        return True, {
            "summary": f"[simulated] {prompt.splitlines()[2] if len(prompt.splitlines()) > 2 else 'Executed task'}",
            "backend": "simulated",
            "model": model,
            "reasoning_effort": reasoning_effort,
            "command_fingerprint": "simulated",
            "exit_code": 0,
            "duration_seconds": 0.2,
            "worktree": str(cwd),
        }

    if backend == "openai":
        backend = "codex"

    if backend == "cursor":
        cmd = [
            config.cursor_command.strip() or "agent",
            "-p",
            "--force",
            "--output-format",
            "json",
            "--model",
            model,
            prompt,
        ]
    else:
        backend = "codex"
        cmd = [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--cd",
            str(cwd),
            "--model",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "-c",
            'approval_policy="never"',
            "--sandbox",
            "workspace-write",
            prompt,
        ]

    started = time.perf_counter()
    try:
        run = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, int(config.timeout_seconds)),
        )
    except FileNotFoundError as exc:
        return False, {
            "reason": "backend_binary_missing",
            "backend": backend,
            "error": str(exc),
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "worktree": str(cwd),
        }
    except subprocess.TimeoutExpired as exc:
        return False, {
            "reason": "backend_timeout",
            "backend": backend,
            "timeout_seconds": int(config.timeout_seconds),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "worktree": str(cwd),
        }
    except Exception as exc:  # noqa: BLE001
        return False, {
            "reason": "backend_execution_error",
            "backend": backend,
            "error": str(exc),
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "worktree": str(cwd),
        }

    duration_seconds = round(max(0.0, time.perf_counter() - started), 3)
    stdout = run.stdout or ""
    stderr = run.stderr or ""

    if backend == "codex":
        text, parse_error = _parse_codex_jsonl(stdout)
    else:
        text, parse_error = _parse_cursor_output(stdout)

    if run.returncode != 0:
        detail = (parse_error or stderr or stdout).strip() or f"exit={run.returncode}"
        return False, {
            "reason": _classify_backend_error(detail),
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "exit_code": int(run.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "error": detail,
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "duration_seconds": duration_seconds,
            "worktree": str(cwd),
        }

    if parse_error:
        return False, {
            "reason": _classify_backend_error(parse_error),
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "exit_code": int(run.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "error": parse_error,
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "duration_seconds": duration_seconds,
            "worktree": str(cwd),
        }
    if not text:
        return False, {
            "reason": "backend_output_malformed",
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "exit_code": int(run.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "error": "no final agent message",
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "duration_seconds": duration_seconds,
            "worktree": str(cwd),
        }
    if _mentions_external_path(text, worktree=cwd):
        return False, {
            "reason": "backend_out_of_worktree_claim",
            "backend": backend,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "exit_code": int(run.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "error": "backend output references changes outside the assigned worktree",
            "command_fingerprint": _command_fingerprint(cmd[:-1]),
            "duration_seconds": duration_seconds,
            "worktree": str(cwd),
        }
    return True, {
        "summary": text,
        "backend": backend,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "exit_code": int(run.returncode),
        "command_fingerprint": _command_fingerprint(cmd[:-1]),
        "duration_seconds": duration_seconds,
        "worktree": str(cwd),
    }
