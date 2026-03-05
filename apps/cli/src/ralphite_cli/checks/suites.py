from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from ralphite_engine import LocalOrchestrator
from ralphite_engine.headless_agent import (
    build_codex_exec_command,
    build_cursor_exec_command,
    normalize_backend_name,
)

from ..core import console

STRICT_SUITES: list[tuple[str, list[str]]] = [
    (
        "parser-compiler",
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "packages/engine/tests/test_task_parser.py",
            "packages/engine/tests/test_structure_compiler.py",
            "-q",
        ],
    ),
    (
        "engine-runtime",
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "packages/engine/tests/test_git_worktree_integration.py",
            "packages/engine/tests/test_orchestrator.py",
            "packages/engine/tests/test_recovery.py",
            "-q",
        ],
    ),
    (
        "cli-contract",
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "apps/cli/tests",
            "-q",
        ],
    ),
    (
        "e2e-recovery",
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "packages/engine/tests/test_e2e_recovery.py",
            "-q",
        ],
    ),
    (
        "fixtures-bootstrap",
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "packages/engine/tests/test_fixture_plan_matrix.py",
            "packages/engine/tests/test_dispatched_plan_consistency.py",
            "packages/engine/tests/test_examples_plans.py",
            "apps/cli/tests/test_bootstrap_e2e.py",
            "-q",
        ],
    ),
]


def _run_strict_checks(
    *,
    repo_root: Path,
    quiet: bool = False,
    machine_mode: bool = False,
    verbose: bool = False,
) -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    capture_subprocess_output = machine_mode or quiet
    for suite_name, command in STRICT_SUITES:
        if not quiet and not machine_mode:
            console.print(f"Running strict check suite [{suite_name}]: {' '.join(command)}")
        result = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        results.append(
            {
                "suite": suite_name,
                "command": " ".join(command),
                "cwd": str(repo_root),
                "exit_code": result.returncode,
                "stdout": result.stdout if capture_subprocess_output and verbose else "",
                "stderr": result.stderr if capture_subprocess_output and verbose else "",
            }
        )
        if result.returncode != 0:
            return False, results
    return True, results


def _run_backend_smoke(
    *,
    orch: LocalOrchestrator,
    repo_root: Path,
    quiet: bool = False,
    machine_mode: bool = False,
    verbose: bool = False,
) -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    capture_subprocess_output = machine_mode or quiet
    backend = normalize_backend_name(str(orch.config.default_backend or "codex"))
    model = str(orch.config.default_model or "gpt-5.3-codex").strip() or "gpt-5.3-codex"
    reasoning_effort = str(orch.config.default_reasoning_effort or "medium").strip().lower() or "medium"
    cursor_command = str(orch.config.cursor_command or "agent").strip() or "agent"

    if backend == "codex":
        command = build_codex_exec_command(
            prompt="Reply with exactly: OK",
            model=model,
            reasoning_effort=reasoning_effort,
            worktree=repo_root,
            sandbox="read-only",
        )
        result = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        row = {
            "suite": "backend-codex-smoke",
            "command": " ".join(command),
            "cwd": str(repo_root),
            "exit_code": result.returncode,
            "stdout": result.stdout if capture_subprocess_output and verbose else "",
            "stderr": result.stderr if capture_subprocess_output and verbose else "",
        }
        results.append(row)
        if result.returncode != 0:
            return False, results
        errors: list[str] = []
        for line in (result.stdout or "").splitlines():
            text = line.strip()
            if not text.startswith("{"):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            ptype = str(payload.get("type", ""))
            if ptype == "error":
                msg = payload.get("message")
                if isinstance(msg, str) and msg.strip():
                    errors.append(msg.strip())
            elif ptype == "turn.failed":
                err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    errors.append(msg.strip())
        if errors:
            row["exit_code"] = 1
            row["stderr"] = errors[0]
            return False, results
    elif backend == "cursor":
        command = build_cursor_exec_command(
            prompt="Reply with exactly: OK",
            model=model,
            cursor_command=cursor_command,
            force=True,
        )
        result = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        row = {
            "suite": "backend-cursor-smoke",
            "command": " ".join(command),
            "cwd": str(repo_root),
            "exit_code": result.returncode,
            "stdout": result.stdout if capture_subprocess_output and verbose else "",
            "stderr": result.stderr if capture_subprocess_output and verbose else "",
        }
        results.append(row)
        if result.returncode != 0:
            return False, results

    return True, results
