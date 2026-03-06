from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ralphite.engine import present_run_status
from ralphite.engine.orchestrator import RunStartBlockedError

from ..core import (
    _emit_payload,
    _git_required_payload,
    _normalize_output,
    _orchestrator,
    _print_run_stream,
    _run_start_blocked_payload,
    _result_payload,
    console,
)


def replay_command(
    run_id: Annotated[str, typer.Argument(help="Existing run id")],
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    output: Annotated[
        str, typer.Option("--output", help="Output mode: stream | table | json")
    ] = "stream",
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Suppress non-critical output")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra event guidance")
    ] = False,
) -> None:
    """Replay a previous run in rerun-failed mode."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    git_status = orch.git_repository_status()
    if not bool(git_status.get("ok")):
        _git_required_payload(
            command="replay",
            workspace=workspace,
            title="Replay",
            output=mode,
            run_id=run_id,
            git_status=git_status,
        )
        raise typer.Exit(code=1)
    execution_status = orch.git_runtime_status()
    dirty_warning = ""
    if bool(execution_status.get("dirty")):
        dirty_warning = (
            "Workspace has uncommitted changes. Replay can proceed, but local edits "
            "may still create merge conflicts."
        )
        if not quiet and mode != "json":
            console.print(f"[yellow]{dirty_warning}[/yellow]")

    try:
        new_run_id = orch.rerun_failed(run_id)
    except RunStartBlockedError as exc:
        _run_start_blocked_payload(
            command="replay",
            title="Replay",
            output=mode,
            preflight=exc.details,
            data={
                "source_run_id": run_id,
                "git": git_status,
                "git_warning": dirty_warning,
            },
        )
        raise typer.Exit(code=1) from exc
    if not quiet and mode != "json":
        console.print(f"Replay started: {new_run_id} (from {run_id})")

    if mode == "stream":
        _print_run_stream(orch, new_run_id, verbose=verbose)
        return

    orch.wait_for_run(new_run_id, timeout=60.0)
    run = orch.get_run(new_run_id)
    status = run.status if run else "unknown"
    payload = _result_payload(
        command="replay",
        ok=status == "succeeded",
        status=status,
        run_id=new_run_id,
        exit_code=0 if status == "succeeded" else 1,
        next_actions=[present_run_status(status).next_action],
        data={
            "source_run_id": run_id,
            "artifacts": run.artifacts if run else [],
            "git": git_status,
            "git_warning": dirty_warning,
        },
    )
    _emit_payload(mode, payload, title="Replay")
    if status != "succeeded":
        raise typer.Exit(code=1)
