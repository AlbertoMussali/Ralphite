from __future__ import annotations

from pathlib import Path
import time
from typing import Annotated

import typer

from ralphite.engine.taxonomy import classify_failure

from ..core import (
    _build_capability_summary,
    _build_execution_summary,
    _emit_payload,
    _find_first_valid_plan,
    _normalize_output,
    _orchestrator,
    _print_preflight_summary,
    _result_payload,
    console,
)
from ..doctoring import _doctor_snapshot, _render_doctor_table


def doctor_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    output: Annotated[
        str, typer.Option("--output", help="Output mode: table | json")
    ] = "table",
    fix_suggestions: Annotated[
        bool, typer.Option("--fix-suggestions", help="Include auto-fix suggestions")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Suppress non-critical output")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra details")
    ] = False,
) -> None:
    """Check local environment, plans, and runtime readiness."""
    started = time.perf_counter()
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    snapshot = _doctor_snapshot(orch, include_fix_suggestions=fix_suggestions)
    selected_plan = _find_first_valid_plan(orch)
    requirements = (
        orch.collect_requirements(plan_ref=str(selected_plan))
        if selected_plan is not None
        else {"tools": [], "mcps": []}
    )
    capabilities = _build_capability_summary(requirements)
    envelope = _result_payload(
        command="doctor",
        ok=bool(snapshot.get("ok")),
        status="succeeded" if bool(snapshot.get("ok")) else "failed",
        run_id=None,
        exit_code=0 if bool(snapshot.get("ok")) else 1,
        issues=[{"code": "doctor.failed", "message": "one or more checks failed"}]
        if not bool(snapshot.get("ok"))
        else [],
        next_actions=(
            [
                *(
                    [
                        classify_failure("git_required").next_action,
                        classify_failure("git_required").command_hint,
                    ]
                    if any(
                        isinstance(item, dict)
                        and item.get("check") == "git-worktree"
                        and str(item.get("status", "")).upper() not in {"OK", "PASS"}
                        for item in snapshot.get("checks", [])
                    )
                    else []
                ),
                *(
                    ["Run with --fix-suggestions to view suggested plan repairs."]
                    if not fix_suggestions
                    else []
                ),
            ]
            if not bool(snapshot.get("ok"))
            else []
        ),
        data={
            **snapshot,
            "execution_summary": _build_execution_summary(
                plan_path=str(selected_plan) if selected_plan is not None else "",
                backend=orch.config.default_backend,
                model=orch.config.default_model,
                reasoning_effort=orch.config.default_reasoning_effort,
                capabilities=capabilities,
                duration_seconds=round(max(0.0, time.perf_counter() - started), 3),
                artifacts_count=0,
            ),
        },
    )
    if mode == "json":
        _emit_payload(mode, envelope)
    else:
        if not quiet:
            _render_doctor_table(snapshot)
            console.print()
            _print_preflight_summary(
                title="Default Run Profile",
                plan_path=str(selected_plan) if selected_plan is not None else "",
                backend=orch.config.default_backend,
                model=orch.config.default_model,
                reasoning_effort=orch.config.default_reasoning_effort,
                capabilities=capabilities,
            )
        if fix_suggestions and snapshot.get("fix_suggestions"):
            console.print("\nSuggested fixes:")
            for row in snapshot.get("fix_suggestions", []):
                if not isinstance(row, dict):
                    continue
                console.print(f"- {row.get('plan_path')}")
                fixes = row.get("fixes") if isinstance(row.get("fixes"), list) else []
                for fix in fixes:
                    if isinstance(fix, dict):
                        console.print(
                            f"  * {fix.get('title')}: {fix.get('description')}"
                        )
        elif verbose and not quiet:
            console.print("No fix suggestions available.")
    if not bool(snapshot.get("ok")):
        raise typer.Exit(code=1)
