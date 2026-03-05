from __future__ import annotations

from pathlib import Path
import time
from typing import Annotated, Any

import typer

from ralphite.engine import present_run_status

from ..core import (
    _bootstrap_plan_file,
    _emit_payload,
    _find_first_valid_plan,
    _normalize_output,
    _orchestrator,
    _print_run_stream,
    _result_payload,
    console,
)
from ..doctoring import (
    _collect_recommended_commands,
    _doctor_evaluation,
    _doctor_snapshot,
    _render_doctor_table,
)


def quickstart_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    goal: Annotated[
        str | None, typer.Option(help="Optional goal to generate a plan")
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
        bool, typer.Option("--yes", help="Auto-approve capabilities")
    ] = False,
    strict_doctor: Annotated[
        bool, typer.Option("--strict-doctor", help="Fail on any doctor warning")
    ] = False,
    bootstrap: Annotated[
        bool,
        typer.Option(
            "--bootstrap/--no-bootstrap",
            help="Auto-init missing config and starter plan",
        ),
    ] = True,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: table | stream | json")
    ] = "table",
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Suppress non-critical output")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra event guidance")
    ] = False,
) -> None:
    """Run guided first-run flow: doctor -> plan -> run."""
    flow_started = time.perf_counter()
    mode = _normalize_output(output)

    workspace_root = workspace.expanduser().resolve()
    config_before = (workspace_root / ".ralphite" / "config.toml").exists()
    plans_dir = workspace_root / ".ralphite" / "plans"
    plans_before = plans_dir.exists() and any(
        path.suffix.lower() in {".yaml", ".yml"} for path in plans_dir.glob("*")
    )
    orch = _orchestrator(workspace, bootstrap=bootstrap)
    bootstrap_paths: list[str] = []

    step_index = 0
    steps: list[dict[str, Any]] = []

    def record_step(name: str, started: float, detail: str = "") -> None:
        nonlocal step_index
        step_index += 1
        elapsed = round(max(0.0, time.perf_counter() - started), 3)
        row = {
            "order": step_index,
            "step": name,
            "elapsed_seconds": elapsed,
            "detail": detail,
        }
        steps.append(row)
        if mode != "json" and not quiet:
            suffix = f" - {detail}" if detail else ""
            console.print(f"{step_index}. {name}{suffix} ({elapsed:.2f}s)")

    if mode != "json" and not quiet:
        console.print("Quickstart Flow")

    doctor_started = time.perf_counter()
    snapshot = _doctor_snapshot(orch, include_fix_suggestions=False)
    blocking_checks, warning_checks = _doctor_evaluation(snapshot, strict=strict_doctor)
    doctor_detail = f"blocking={len(blocking_checks)} warnings={len(warning_checks)} strict={strict_doctor}"
    record_step("Doctor", doctor_started, doctor_detail)
    if blocking_checks:
        recommended = _collect_recommended_commands(snapshot)
        next_actions = recommended or [
            "Run `ralphite doctor --output table` to inspect failures."
        ]
        if mode == "json":
            _emit_payload(
                mode,
                _result_payload(
                    command="quickstart",
                    ok=False,
                    status="failed",
                    run_id=None,
                    exit_code=1,
                    issues=[
                        {"code": "doctor.failed", "message": "workspace checks failed"}
                    ],
                    next_actions=next_actions,
                    data={
                        "doctor": snapshot,
                        "strict_doctor": strict_doctor,
                        "warnings": warning_checks,
                        "step_timing": steps,
                        "total_elapsed_seconds": round(
                            max(0.0, time.perf_counter() - flow_started), 3
                        ),
                    },
                ),
                title="Quickstart",
            )
        else:
            _render_doctor_table(snapshot)
            if recommended:
                console.print("Recommended commands:")
                for cmd in recommended:
                    console.print(f"- {cmd}")
        raise typer.Exit(code=1)

    bootstrap_started = time.perf_counter()
    selected_bootstrap_plan = _find_first_valid_plan(orch)
    if bootstrap and selected_bootstrap_plan is None:
        selected_bootstrap_plan = _bootstrap_plan_file(
            orch,
            template="general_sps",
            goal=goal,
            plan_id="starter_loop",
            name="Starter Loop",
            lanes=["lane_a", "lane_b"],
            loop_unit="per_task",
        )
        bootstrap_paths.append(str(selected_bootstrap_plan))
    if bootstrap:
        config_after = orch.paths["config"].exists()
        plans_after = bool(orch.list_plans())
        if not config_before and config_after:
            bootstrap_paths.append(str(orch.paths["config"]))
        if not plans_before and plans_after:
            bootstrap_paths.append(str(orch.paths["plans"]))
    record_step(
        "Bootstrap", bootstrap_started, "created" if bootstrap_paths else "ready"
    )
    if bootstrap and mode != "json" and not quiet and bootstrap_paths:
        console.print(
            f"Bootstrap: initialized {', '.join(dict.fromkeys(bootstrap_paths))}"
        )

    plan_started = time.perf_counter()
    plan_ref: str | None = None
    if goal:
        plan_ref = str(orch.goal_to_plan(goal))
    else:
        preferred = selected_bootstrap_plan or _find_first_valid_plan(orch)
        if preferred is None:
            payload = _result_payload(
                command="quickstart",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[
                    {
                        "code": "quickstart.no_valid_plan",
                        "message": "no valid v1 plan found",
                    }
                ],
                next_actions=[
                    "Run `ralphite init --workspace . --yes` to generate a valid v1 plan."
                ],
                data={
                    "step_timing": steps,
                    "bootstrap_paths": list(dict.fromkeys(bootstrap_paths)),
                    "total_elapsed_seconds": round(
                        max(0.0, time.perf_counter() - flow_started), 3
                    ),
                },
            )
            _emit_payload(mode, payload, title="Quickstart")
            raise typer.Exit(code=1)
        plan_ref = str(preferred)
    record_step(
        "Plan Selection",
        plan_started,
        Path(plan_ref).name if isinstance(plan_ref, str) else "none",
    )

    approval_started = time.perf_counter()
    requirements = orch.collect_requirements(plan_ref=plan_ref)
    approved = True
    if not quiet and mode != "json":
        console.print(f"Required tools: {requirements['tools'] or ['none']}")
        console.print(f"Required mcps: {requirements['mcps'] or ['none']}")

    if not yes:
        approved = typer.confirm(
            "Approve these capabilities for this run?", default=True
        )
        if not approved:
            record_step("Capability Approval", approval_started, "cancelled")
            payload = _result_payload(
                command="quickstart",
                ok=False,
                status="cancelled",
                run_id=None,
                exit_code=1,
                issues=[
                    {"code": "quickstart.cancelled", "message": "run aborted by user"}
                ],
                data={
                    "step_timing": steps,
                    "bootstrap_paths": list(dict.fromkeys(bootstrap_paths)),
                    "total_elapsed_seconds": round(
                        max(0.0, time.perf_counter() - flow_started), 3
                    ),
                },
            )
            _emit_payload(mode, payload, title="Quickstart")
            raise typer.Exit(code=1)
    record_step(
        "Capability Approval", approval_started, "approved" if approved else "cancelled"
    )

    run_started = time.perf_counter()
    run_id = orch.start_run(
        plan_ref=plan_ref,
        backend_override=backend,
        model_override=model,
        reasoning_effort_override=reasoning_effort,
        metadata={"source": "cli.quickstart", "goal": goal},
    )
    run_status = "running"
    if mode == "stream":
        _print_run_stream(orch, run_id, verbose=verbose)
        run_state = orch.get_run(run_id)
        run_status = run_state.status if run_state else "unknown"
        record_step("Run", run_started, run_status)
        return

    orch.wait_for_run(run_id, timeout=60.0)
    run = orch.get_run(run_id)
    status = run.status if run else "unknown"
    run_status = status
    record_step("Run", run_started, status)
    payload = _result_payload(
        command="quickstart",
        ok=status == "succeeded",
        status=status,
        run_id=run_id,
        exit_code=0 if status == "succeeded" else 1,
        next_actions=[
            present_run_status(status).next_action,
            "Use `ralphite history --workspace . --output table` to inspect run history.",
        ],
        data={
            "artifacts": run.artifacts if run else [],
            "plan_path": str(plan_ref) if plan_ref else "",
            "required_tools": requirements["tools"],
            "required_mcps": requirements["mcps"],
            "step_timing": steps,
            "doctor_warnings": warning_checks,
            "bootstrap_paths": list(dict.fromkeys(bootstrap_paths)),
            "backend": backend or orch.config.default_backend,
            "model": model or orch.config.default_model,
            "reasoning_effort": reasoning_effort
            or orch.config.default_reasoning_effort,
            "total_elapsed_seconds": round(
                max(0.0, time.perf_counter() - flow_started), 3
            ),
        },
    )
    _emit_payload(mode, payload, title="Quickstart")
    if status != "succeeded":
        raise typer.Exit(code=1)
