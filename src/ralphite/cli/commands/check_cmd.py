from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Annotated

import typer

from ..checks.suites import _run_backend_smoke, _run_strict_checks
from ..core import (
    _emit_payload,
    _normalize_output,
    _orchestrator,
    _repo_root,
    _result_payload,
    console,
)
from ..doctoring import _doctor_snapshot, _render_doctor_table
from ..doctoring import _doctor_evaluation


def check_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    full: Annotated[
        bool, typer.Option("--full", help="Run full repo test suite")
    ] = False,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Run strict internal checks (doctor + backend smoke + validation suites)",
        ),
    ] = False,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: table | json")
    ] = "table",
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Suppress non-critical output")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra details")
    ] = False,
) -> None:
    """Run baseline quality gates for local CLI reliability."""
    orch = _orchestrator(workspace)
    repo_root = _repo_root()
    mode = _normalize_output(output)
    machine_mode = mode == "json"
    capture_subprocess_output = machine_mode or quiet

    snapshot = _doctor_snapshot(orch, include_fix_suggestions=False)
    blocking_checks, _warning_checks = _doctor_evaluation(snapshot, strict=strict)
    if not machine_mode and not quiet and not strict:
        _render_doctor_table(snapshot)
    command_results: list[dict[str, object]] = []
    if strict and (blocking_checks or not bool(snapshot.get("ok"))):
        envelope = _result_payload(
            command="check",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=1,
            issues=[
                {
                    "code": "check.strict_doctor_failed",
                    "message": "doctor checks failed for strict checks",
                }
            ],
            data={"doctor": snapshot, "commands": command_results},
        )
        _emit_payload(mode, envelope, title="Check")
        raise typer.Exit(code=1)
    if not strict and not bool(snapshot.get("ok")):
        envelope = _result_payload(
            command="check",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=1,
            issues=[{"code": "check.doctor_failed", "message": "doctor checks failed"}],
            data={"doctor": snapshot, "commands": command_results},
        )
        _emit_payload(mode, envelope, title="Check")
        raise typer.Exit(code=1)

    compile_targets: list[str] = []
    for relative in ("src/ralphite",):
        target = repo_root / relative
        if target.exists():
            compile_targets.append(relative)
    if compile_targets:
        compile_cmd = [sys.executable, "-m", "compileall", *compile_targets]
        compile_result = subprocess.run(
            compile_cmd,
            cwd=repo_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        command_results.append(
            {
                "command": " ".join(compile_cmd),
                "exit_code": compile_result.returncode,
                "stdout": compile_result.stdout
                if capture_subprocess_output and verbose
                else "",
                "stderr": compile_result.stderr
                if capture_subprocess_output and verbose
                else "",
            }
        )
        if compile_result.returncode != 0:
            envelope = _result_payload(
                command="check",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[
                    {"code": "check.compile_failed", "message": "compileall failed"}
                ],
                data={"doctor": snapshot, "commands": command_results},
            )
            _emit_payload(mode, envelope, title="Check")
            raise typer.Exit(code=1)

    if full:
        command = [
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "tests/engine",
            "tests/cli",
            "-q",
        ]
        if not quiet and not machine_mode:
            console.print(f"Running: {' '.join(command)}")
        result = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        command_results.append(
            {
                "command": " ".join(command),
                "exit_code": result.returncode,
                "stdout": result.stdout
                if capture_subprocess_output and verbose
                else "",
                "stderr": result.stderr
                if capture_subprocess_output and verbose
                else "",
            }
        )
        if result.returncode != 0:
            envelope = _result_payload(
                command="check",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[
                    {"code": "check.pytest_failed", "message": "full test suite failed"}
                ],
                data={"doctor": snapshot, "commands": command_results},
            )
            _emit_payload(mode, envelope, title="Check")
            raise typer.Exit(code=1)

    if strict:
        smoke_ok, smoke_results = _run_backend_smoke(
            orch=orch,
            repo_root=repo_root,
            quiet=quiet,
            machine_mode=machine_mode,
            verbose=verbose,
        )
        command_results.extend(smoke_results)
        if not smoke_ok:
            envelope = _result_payload(
                command="check",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[
                    {
                        "code": "check.backend_smoke_failed",
                        "message": "backend smoke check failed",
                    }
                ],
                data={"doctor": snapshot, "commands": command_results},
            )
            _emit_payload(mode, envelope, title="Check")
            raise typer.Exit(code=1)

    if strict:
        gate_ok, gate_results = _run_strict_checks(
            repo_root=repo_root,
            quiet=quiet,
            machine_mode=machine_mode,
            verbose=verbose,
        )
        command_results.extend(gate_results)
        if not gate_ok:
            envelope = _result_payload(
                command="check",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[
                    {
                        "code": "check.strict_failed",
                        "message": "strict check suite failed",
                    }
                ],
                data={"doctor": snapshot, "commands": command_results},
            )
            _emit_payload(mode, envelope, title="Check")
            raise typer.Exit(code=1)

    envelope = _result_payload(
        command="check",
        ok=True,
        status="succeeded",
        run_id=None,
        exit_code=0,
        data={"doctor": snapshot, "commands": command_results},
    )
    if machine_mode:
        _emit_payload(mode, envelope)
    elif not quiet:
        console.print("[green]ralphite check passed[/green]")
