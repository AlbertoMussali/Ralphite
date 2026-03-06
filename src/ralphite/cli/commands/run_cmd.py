from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ralphite.engine import present_run_status

from ..core import (
    _build_capability_summary,
    _build_execution_summary,
    _emit_payload,
    _git_required_payload,
    _normalize_output,
    _orchestrator,
    _print_preflight_summary,
    _print_run_stream,
    _resolve_plan_ref,
    _result_payload,
    console,
)


def run_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    goal: Annotated[
        str | None, typer.Option(help="Goal text to generate a plan")
    ] = None,
    backend: Annotated[
        str | None, typer.Option(help="Execution backend override: codex | cursor")
    ] = None,
    model: Annotated[
        str | None, typer.Option(help="Model override for headless backend")
    ] = None,
    reasoning_effort: Annotated[
        str | None, typer.Option(help="Reasoning effort override: low | medium | high")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Auto-approve requirements")
    ] = False,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: stream | table | json")
    ] = "stream",
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Suppress non-critical run output")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra event guidance")
    ] = False,
) -> None:
    """Run a plan immediately in headless mode."""
    orch = _orchestrator(workspace)
    output_mode = _normalize_output(output)
    git_status = orch.git_runtime_status()
    if not bool(git_status.get("ok")):
        _git_required_payload(
            command="run",
            workspace=workspace,
            title="Run Result",
            output=output_mode,
            git_status=git_status,
        )
        raise typer.Exit(code=1)
    selected_backend = backend or orch.config.default_backend
    selected_model = model or orch.config.default_model
    selected_reasoning_effort = reasoning_effort or orch.config.default_reasoning_effort

    plan_ref = plan
    if goal:
        generated = orch.goal_to_plan(goal)
        plan_ref = str(generated)
    selected_plan_path = str(_resolve_plan_ref(orch, plan_ref))

    requirements = orch.collect_requirements(plan_ref=plan_ref)
    capabilities = _build_capability_summary(requirements)
    if not quiet and output_mode != "json":
        _print_preflight_summary(
            title="Run Preflight",
            plan_path=selected_plan_path,
            backend=selected_backend,
            model=selected_model,
            reasoning_effort=selected_reasoning_effort,
            capabilities=capabilities,
        )

    if not yes:
        approved = typer.confirm(
            "Approve these capabilities for this run?", default=True
        )
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
        console.print(
            f"Watch this run: `uv run ralphite watch --workspace {workspace.expanduser().resolve()} --run-id {run_id}`"
        )

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
            "plan_path": selected_plan_path,
            "required_tools": requirements["tools"],
            "required_mcps": requirements["mcps"],
            "backend": selected_backend,
            "model": selected_model,
            "reasoning_effort": selected_reasoning_effort,
            "execution_summary": _build_execution_summary(
                plan_path=selected_plan_path,
                backend=selected_backend,
                model=selected_model,
                reasoning_effort=selected_reasoning_effort,
                capabilities=capabilities,
                duration_seconds=(
                    run_state.metadata.get("run_metrics", {}).get("total_seconds", 0.0)
                    if run_state
                    and isinstance(run_state.metadata.get("run_metrics"), dict)
                    else 0.0
                ),
                artifacts_count=(len(run_state.artifacts) if run_state else 0),
            ),
        },
    )
    _emit_payload(output_mode, payload, title="Run Result")
    if status != "succeeded":
        raise typer.Exit(code=1)
