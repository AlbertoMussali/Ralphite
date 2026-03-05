from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..core import (
    _emit_payload,
    _normalize_output,
    _orchestrator,
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
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    snapshot = _doctor_snapshot(orch, include_fix_suggestions=fix_suggestions)
    envelope = _result_payload(
        command="doctor",
        ok=bool(snapshot.get("ok")),
        status="succeeded" if bool(snapshot.get("ok")) else "failed",
        run_id=None,
        exit_code=0 if bool(snapshot.get("ok")) else 1,
        issues=[{"code": "doctor.failed", "message": "one or more checks failed"}]
        if not bool(snapshot.get("ok"))
        else [],
        next_actions=["Run with --fix-suggestions to view suggested plan repairs."]
        if fix_suggestions
        else [],
        data=snapshot,
    )
    if mode == "json":
        _emit_payload(mode, envelope)
    else:
        if not quiet:
            _render_doctor_table(snapshot)
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
