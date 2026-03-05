from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ralphite_engine import present_run_status

from ..core import (
    _emit_payload,
    _normalize_output,
    _orchestrator,
    _print_run_stream,
    _result_payload,
    console,
)
from ..exit_codes import (
    RECOVER_EXIT_INTERNAL_ERROR,
    RECOVER_EXIT_INVALID_INPUT,
    RECOVER_EXIT_NO_RECOVERABLE,
    RECOVER_EXIT_PENDING,
    RECOVER_EXIT_PREFLIGHT_FAILED,
    RECOVER_EXIT_SUCCESS,
    RECOVER_EXIT_TERMINAL_FAILURE,
    RECOVER_EXIT_UNRECOVERABLE,
)


def recover_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str | None, typer.Option(help="Run id to recover")] = None,
    mode: Annotated[
        str,
        typer.Option(help="Recovery mode: manual | agent_best_effort | abort_phase"),
    ] = "manual",
    prompt: Annotated[
        str | None, typer.Option(help="Prompt used by agent_best_effort mode")
    ] = None,
    preflight_only: Annotated[
        bool, typer.Option("--preflight-only", help="Validate recovery readiness only")
    ] = False,
    resume: Annotated[
        bool,
        typer.Option(
            "--resume/--no-resume", help="Resume immediately after setting mode"
        ),
    ] = True,
    json_mode: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable output")
    ] = False,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: stream | table | json")
    ] = "table",
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Suppress non-critical output")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra event guidance")
    ] = False,
) -> None:
    """Recover and resume a checkpointed run with explicit recovery mode and automation semantics."""
    orch = _orchestrator(workspace)
    output_mode = _normalize_output(output, json_mode=json_mode)

    allowed_modes = {"manual", "agent_best_effort", "abort_phase"}
    if mode not in allowed_modes:
        payload = _result_payload(
            command="recover",
            ok=False,
            status="failed",
            run_id=run_id,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[
                {"code": "recover.invalid_mode", "message": f"invalid mode '{mode}'"}
            ],
            next_actions=["Use one of: manual, agent_best_effort, abort_phase."],
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

    target = run_id
    if target is None:
        recoverable = orch.list_recoverable_runs()
        if not recoverable:
            payload = _result_payload(
                command="recover",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=RECOVER_EXIT_NO_RECOVERABLE,
                issues=[
                    {"code": "recover.none", "message": "no recoverable runs found"}
                ],
                next_actions=["Run `ralphite history` to inspect previous runs."],
            )
            _emit_payload(output_mode, payload, title="Recovery")
            raise typer.Exit(code=RECOVER_EXIT_NO_RECOVERABLE)
        target = recoverable[-1]

    if not orch.recover_run(target):
        payload = _result_payload(
            command="recover",
            ok=False,
            status="failed",
            run_id=target,
            exit_code=RECOVER_EXIT_UNRECOVERABLE,
            issues=[
                {
                    "code": "recover.unrecoverable",
                    "message": "run not found or unrecoverable",
                }
            ],
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_UNRECOVERABLE)

    if not orch.set_recovery_mode(target, mode, prompt=prompt):
        payload = _result_payload(
            command="recover",
            ok=False,
            status="failed",
            run_id=target,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[
                {
                    "code": "recover.mode_set_failed",
                    "message": f"unable to set recovery mode '{mode}'",
                }
            ],
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

    preflight = orch.recovery_preflight(target)
    if preflight_only:
        exit_code = (
            RECOVER_EXIT_SUCCESS
            if preflight.get("ok")
            else RECOVER_EXIT_PREFLIGHT_FAILED
        )
        issues = (
            []
            if preflight.get("ok")
            else [{"code": "recover.preflight_failed", "message": "preflight failed"}]
        )
        payload = _result_payload(
            command="recover",
            ok=bool(preflight.get("ok")),
            status="succeeded" if preflight.get("ok") else "failed",
            run_id=target,
            exit_code=exit_code,
            issues=issues,
            next_actions=list(preflight.get("blocking_reasons", []))
            if isinstance(preflight, dict)
            else [],
            data={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery Preflight")
        raise typer.Exit(code=exit_code)

    if not preflight.get("ok"):
        payload = _result_payload(
            command="recover",
            ok=False,
            status="failed",
            run_id=target,
            exit_code=RECOVER_EXIT_PREFLIGHT_FAILED,
            issues=[
                {
                    "code": "recover.preflight_failed",
                    "message": "recovery preflight failed",
                }
            ],
            next_actions=list(preflight.get("blocking_reasons", []))
            if isinstance(preflight, dict)
            else [],
            data={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PREFLIGHT_FAILED)

    if not resume:
        payload = _result_payload(
            command="recover",
            ok=True,
            status="paused",
            run_id=target,
            exit_code=RECOVER_EXIT_PENDING,
            next_actions=["Run `ralphite recover --resume` to continue."],
            data={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PENDING)

    resumed = orch.resume_from_checkpoint(target)
    if not resumed:
        latest_preflight = orch.recovery_preflight(target)
        payload = _result_payload(
            command="recover",
            ok=False,
            status="paused_recovery_required",
            run_id=target,
            exit_code=RECOVER_EXIT_PENDING,
            issues=[{"code": "recover.resume_rejected", "message": "resume rejected"}],
            next_actions=(
                list(latest_preflight.get("blocking_reasons", []))
                if isinstance(latest_preflight, dict)
                else []
            ),
            data={"preflight": latest_preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PENDING)

    if output_mode == "stream":
        _print_run_stream(orch, target, verbose=verbose)
    else:
        orch.wait_for_run(target, timeout=60.0)

    run = orch.get_run(target)
    status = run.status if run else "unknown"
    if status == "succeeded":
        exit_code = RECOVER_EXIT_SUCCESS
    elif status in {"paused", "paused_recovery_required"}:
        exit_code = RECOVER_EXIT_PENDING
    elif status in {"failed", "cancelled"}:
        exit_code = RECOVER_EXIT_TERMINAL_FAILURE
    else:
        exit_code = RECOVER_EXIT_INTERNAL_ERROR
    payload = _result_payload(
        command="recover",
        ok=exit_code == RECOVER_EXIT_SUCCESS,
        status=status,
        run_id=target,
        exit_code=exit_code,
        next_actions=[present_run_status(status).next_action],
        data={"artifacts": run.artifacts if run else []},
    )
    _emit_payload(output_mode, payload, title="Recovery Result")
    if exit_code != RECOVER_EXIT_SUCCESS and output_mode != "stream":
        raise typer.Exit(code=exit_code)
    if not quiet and output_mode != "json":
        console.print(f"Recovery mode set and resumed for run: [bold]{target}[/bold]")
