from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ralphite.engine import present_recovery_mode

from ..core import (
    _emit_payload,
    _git_required_payload,
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


def _primary_failure_reason(run: object) -> str:
    metadata = (
        getattr(run, "metadata", {})
        if isinstance(getattr(run, "metadata", {}), dict)
        else {}
    )
    metrics = metadata.get("run_metrics", {})
    if not isinstance(metrics, dict):
        return ""
    histogram = metrics.get("interruption_reason_counts", {})
    if not isinstance(histogram, dict) or not histogram:
        histogram = metrics.get("failure_reason_counts", {})
    if not isinstance(histogram, dict) or not histogram:
        return ""
    ranked = sorted(
        [(str(code), int(count)) for code, count in histogram.items()],
        key=lambda item: item[1],
        reverse=True,
    )
    code, count = ranked[0]
    return f"{code} ({count})"


def _recovery_details(run: object) -> dict[str, object]:
    metadata = (
        getattr(run, "metadata", {})
        if isinstance(getattr(run, "metadata", {}), dict)
        else {}
    )
    recovery = metadata.get("recovery", {})
    return recovery if isinstance(recovery, dict) else {}


def _recommend_recovery_mode(
    *,
    preflight: dict[str, object] | None,
    run: object,
) -> tuple[str, str, str]:
    recovery = _recovery_details(run)
    details = recovery.get("details")
    details = details if isinstance(details, dict) else {}
    conflict_files = (
        preflight.get("conflict_files")
        if isinstance(preflight, dict)
        and isinstance(preflight.get("conflict_files"), list)
        else []
    )
    unresolved_conflicts = (
        preflight.get("unresolved_conflict_files")
        if isinstance(preflight, dict)
        and isinstance(preflight.get("unresolved_conflict_files"), list)
        else []
    )
    blocking_reasons = (
        preflight.get("blocking_reasons")
        if isinstance(preflight, dict)
        and isinstance(preflight.get("blocking_reasons"), list)
        else []
    )
    reason = str(details.get("reason") or "").strip()
    prompt_present = bool(str(recovery.get("prompt") or "").strip())

    if unresolved_conflicts or conflict_files:
        return (
            "manual",
            present_recovery_mode("manual"),
            "Conflict files are present. Resolve merge markers manually before resuming.",
        )
    if reason == "base_integration_blocked_by_local_changes":
        return (
            "manual",
            present_recovery_mode("manual"),
            "The primary workspace has overlapping local edits. Preserve those edits and resolve the overlap manually before resuming.",
        )
    if reason in {"worktree_prepare_failed", "phase_worktree_add_failed"}:
        return (
            "abort_phase",
            present_recovery_mode("abort_phase"),
            "Recovery state indicates a phase-level worktree failure. Abort the phase instead of attempting in-place remediation.",
        )
    if any(
        isinstance(item, str) and "unrecoverable" in item.lower()
        for item in blocking_reasons
    ):
        return (
            "abort_phase",
            present_recovery_mode("abort_phase"),
            "The run is not safely recoverable from the current phase state.",
        )
    if reason in {"base_merge_conflict", "merge_conflict"}:
        return (
            "agent_best_effort",
            present_recovery_mode("agent_best_effort"),
            (
                "No unresolved merge markers were detected in the recovery worktree. "
                "Use agent-assisted recovery to finish the merge."
            )
            if prompt_present
            else (
                "No unresolved merge markers were detected in the recovery worktree. "
                "Agent-assisted recovery is the best next step if you provide a remediation prompt."
            ),
        )
    return (
        "manual",
        present_recovery_mode("manual"),
        "Manual recovery is the conservative default when runtime context is limited.",
    )


def _recommended_next_action(
    *,
    recommended_mode: str,
    recommended_reason: str,
    preflight: dict[str, object] | None,
    run_id: str,
) -> str:
    blockers = (
        preflight.get("blocking_reasons")
        if isinstance(preflight, dict)
        and isinstance(preflight.get("blocking_reasons"), list)
        else []
    )
    if blockers:
        blocker = str(blockers[0]).strip()
        if blocker:
            return blocker
    if recommended_mode == "manual":
        return f"Resolve the reported conflicts, then rerun `ralphite recover --workspace . --run-id {run_id} --mode manual --resume`."
    if recommended_mode == "agent_best_effort":
        return f'Provide a recovery prompt, then rerun `ralphite recover --workspace . --run-id {run_id} --mode agent_best_effort --prompt "resolve conflicts" --resume`.'
    if recommended_mode == "abort_phase":
        return f"Abort the blocked phase with `ralphite recover --workspace . --run-id {run_id} --mode abort_phase --resume`."
    return recommended_reason


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
    git_status = orch.git_repository_status()
    if not bool(git_status.get("ok")):
        _git_required_payload(
            command="recover",
            workspace=workspace,
            title="Recovery",
            output=output_mode,
            run_id=run_id,
            git_status=git_status,
        )
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)
    execution_status = orch.git_runtime_status()
    dirty_warning = ""
    if bool(execution_status.get("dirty")):
        dirty_warning = (
            "Workspace has uncommitted changes. Recovery can proceed, but local edits "
            "may still create merge conflicts."
        )
        if output_mode != "json" and not quiet:
            console.print(f"[yellow]{dirty_warning}[/yellow]")

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
    recovery_label = present_recovery_mode(mode)
    recovered_run = orch.get_run(target)
    (
        recommended_mode,
        recommended_mode_label,
        recommended_reason,
    ) = _recommend_recovery_mode(preflight=preflight, run=recovered_run)
    recommended_next_action = _recommended_next_action(
        recommended_mode=recommended_mode,
        recommended_reason=recommended_reason,
        preflight=preflight,
        run_id=target,
    )
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
            next_actions=[recommended_next_action],
            data={
                "preflight": preflight,
                "plan_path": recovered_run.plan_path if recovered_run else "",
                "recovery_mode": mode,
                "recovery_mode_label": recovery_label,
                "recommended_recovery_mode": recommended_mode,
                "recommended_recovery_mode_label": recommended_mode_label,
                "recommended_recovery_reason": recommended_reason,
                "primary_failure_reason": _primary_failure_reason(recovered_run),
                "git": git_status,
                "git_warning": dirty_warning,
            },
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
            next_actions=[recommended_next_action],
            data={
                "preflight": preflight,
                "plan_path": recovered_run.plan_path if recovered_run else "",
                "recovery_mode": mode,
                "recovery_mode_label": recovery_label,
                "recommended_recovery_mode": recommended_mode,
                "recommended_recovery_mode_label": recommended_mode_label,
                "recommended_recovery_reason": recommended_reason,
                "primary_failure_reason": _primary_failure_reason(recovered_run),
                "git": git_status,
                "git_warning": dirty_warning,
            },
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
            next_actions=[recommended_next_action],
            data={
                "preflight": preflight,
                "plan_path": recovered_run.plan_path if recovered_run else "",
                "recovery_mode": mode,
                "recovery_mode_label": recovery_label,
                "recommended_recovery_mode": recommended_mode,
                "recommended_recovery_mode_label": recommended_mode_label,
                "recommended_recovery_reason": recommended_reason,
                "primary_failure_reason": _primary_failure_reason(recovered_run),
                "git": git_status,
                "git_warning": dirty_warning,
            },
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
            next_actions=[
                _recommended_next_action(
                    recommended_mode=recommended_mode,
                    recommended_reason=recommended_reason,
                    preflight=latest_preflight
                    if isinstance(latest_preflight, dict)
                    else None,
                    run_id=target,
                )
            ],
            data={
                "preflight": latest_preflight,
                "plan_path": recovered_run.plan_path if recovered_run else "",
                "recovery_mode": mode,
                "recovery_mode_label": recovery_label,
                "recommended_recovery_mode": recommended_mode,
                "recommended_recovery_mode_label": recommended_mode_label,
                "recommended_recovery_reason": recommended_reason,
                "primary_failure_reason": _primary_failure_reason(recovered_run),
                "git": git_status,
                "git_warning": dirty_warning,
            },
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
        next_actions=[
            recommended_next_action,
        ],
        data={
            "artifacts": run.artifacts if run else [],
            "plan_path": run.plan_path if run else "",
            "preflight": preflight,
            "recovery_mode": mode,
            "recovery_mode_label": recovery_label,
            "recommended_recovery_mode": recommended_mode,
            "recommended_recovery_mode_label": recommended_mode_label,
            "recommended_recovery_reason": recommended_reason,
            "primary_failure_reason": _primary_failure_reason(run),
            "git": git_status,
            "git_warning": dirty_warning,
        },
    )
    _emit_payload(output_mode, payload, title="Recovery Result")
    if exit_code != RECOVER_EXIT_SUCCESS and output_mode != "stream":
        raise typer.Exit(code=exit_code)
    if not quiet and output_mode != "json":
        console.print(
            f"Recovery mode set and resumed for run: [bold]{target}[/bold] ({recovery_label})"
        )
