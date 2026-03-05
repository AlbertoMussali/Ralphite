from __future__ import annotations

from pathlib import Path
from typing import Annotated

from rich.table import Table
import typer

from ralphite_engine import present_run_status

from ..core import (
    _emit_payload,
    _normalize_output,
    _orchestrator,
    _result_payload,
    console,
)


def history_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    query: Annotated[str | None, typer.Option(help="Search by id/status/path")] = None,
    limit: Annotated[int, typer.Option(help="Max rows")] = 20,
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
    """Show local run history."""
    orch = _orchestrator(workspace)
    rows = orch.list_history(limit=limit, query=query)
    mode = _normalize_output(output)

    rows_payload = [
        {
            "run_id": run.id,
            "status": run.status,
            "status_label": present_run_status(run.status).label,
            "next_action": present_run_status(run.status).next_action,
            "plan": run.plan_path,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "duration_seconds": (
                run.metadata.get("run_metrics", {}).get("total_seconds")
                if isinstance(run.metadata.get("run_metrics"), dict)
                else None
            ),
            "retry_count": int(run.retry_count or 0),
            "failure_reasons": (
                run.metadata.get("run_metrics", {}).get("failure_reason_counts")
                if isinstance(run.metadata.get("run_metrics"), dict)
                else {}
            ),
        }
        for run in rows
    ]
    envelope = _result_payload(
        command="history",
        ok=True,
        status="succeeded",
        run_id=None,
        exit_code=0,
        data={"rows": rows_payload, "query": query, "limit": limit},
    )
    if mode == "json":
        _emit_payload(mode, envelope)
        return

    if verbose and not quiet:
        console.print(f"Rows returned: {len(rows_payload)}")

    table = Table(title="Run History")
    table.add_column("Run ID")
    table.add_column("Status")
    table.add_column("Next Action")
    table.add_column("Plan")
    table.add_column("Created")
    table.add_column("Completed")
    table.add_column("Duration(s)")
    table.add_column("Retries")
    for run in rows:
        status = present_run_status(run.status)
        metrics = (
            run.metadata.get("run_metrics", {})
            if isinstance(run.metadata.get("run_metrics"), dict)
            else {}
        )
        duration = metrics.get("total_seconds", "-")
        table.add_row(
            run.id,
            status.label,
            status.next_action,
            run.plan_path,
            run.created_at,
            run.completed_at or "-",
            str(duration),
            str(run.retry_count),
        )
    console.print(table)
