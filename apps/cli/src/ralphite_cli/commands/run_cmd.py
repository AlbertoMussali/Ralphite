from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ralphite_engine import present_run_status

from ..core import _emit_payload, _normalize_output, _orchestrator, _print_run_stream, _result_payload, console


def run_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    goal: Annotated[str | None, typer.Option(help="Goal text to generate a plan")] = None,
    backend: Annotated[
        str | None, typer.Option(help="Execution backend override: codex | cursor")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Model override for headless backend")] = None,
    reasoning_effort: Annotated[
        str | None, typer.Option(help="Reasoning effort override: low | medium | high")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Auto-approve requirements")] = False,
    output: Annotated[str, typer.Option("--output", help="Output mode: stream | table | json")] = "stream",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical run output")] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra event guidance")
    ] = False,
) -> None:
    """Run a plan immediately in headless mode."""
    orch = _orchestrator(workspace)
    output_mode = _normalize_output(output)

    plan_ref = plan
    if goal:
        generated = orch.goal_to_plan(goal)
        plan_ref = str(generated)
        if not quiet and output_mode != "json":
            console.print(f"Generated plan from goal: {generated}")

    requirements = orch.collect_requirements(plan_ref=plan_ref)
    if not quiet and output_mode != "json":
        console.print(f"Required tools: {requirements['tools'] or ['none']}")
        console.print(f"Required mcps: {requirements['mcps'] or ['none']}")

    if not yes:
        approved = typer.confirm("Approve these capabilities for this run?", default=True)
        if not approved:
            if not quiet and output_mode != "json":
                console.print("Run aborted by user.")
            raise typer.Exit(code=1)

    run_id = orch.start_run(
        plan_ref=plan_ref,
        backend_override=backend,
        model_override=model,
        reasoning_effort_override=reasoning_effort,
        metadata={"source": "cli.run", "goal": goal},
    )
    if not quiet and output_mode != "json":
        console.print(f"Started run: [bold]{run_id}[/bold]")

    if output_mode == "stream":
        _print_run_stream(orch, run_id, verbose=verbose)
        return

    orch.wait_for_run(run_id, timeout=60.0)
    run_state = orch.get_run(run_id)
    status = run_state.status if run_state else "unknown"
    payload = _result_payload(
        command="run",
        ok=status == "succeeded",
        status=status,
        run_id=run_id,
        exit_code=0 if status == "succeeded" else 1,
        next_actions=[present_run_status(status).next_action],
        data={
            "artifacts": run_state.artifacts if run_state else [],
            "plan_path": str(plan_ref or ""),
            "required_tools": requirements["tools"],
            "required_mcps": requirements["mcps"],
            "backend": backend or orch.config.default_backend,
            "model": model or orch.config.default_model,
            "reasoning_effort": reasoning_effort or orch.config.default_reasoning_effort,
        },
    )
    _emit_payload(output_mode, payload, title="Run Result")
    if status != "succeeded":
        raise typer.Exit(code=1)
