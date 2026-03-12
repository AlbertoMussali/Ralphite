from __future__ import annotations

from pathlib import Path
from typing import Annotated

from rich.table import Table
import typer

from ..core import (
    _emit_payload,
    _normalize_output,
    _orchestrator,
    _result_payload,
    console,
)


def reconcile_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str, typer.Option(help="Run ID to reconcile")] = "",
    apply: Annotated[
        bool, typer.Option("--apply", help="Persist repaired run/checkpoint state")
    ] = False,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: table | json")
    ] = "table",
) -> None:
    """Rebuild a run summary from persisted state and current git/worktree truth."""
    mode = _normalize_output(output)
    if not run_id.strip():
        payload = _result_payload(
            command="reconcile",
            ok=False,
            status="failed",
            exit_code=1,
            issues=[
                {
                    "code": "reconcile.run_id_required",
                    "message": "--run-id is required",
                }
            ],
        )
        _emit_payload(mode, payload, title="Reconcile Result")
        raise typer.Exit(code=1)

    orch = _orchestrator(workspace)
    result = orch.reconcile_run(run_id.strip(), apply=apply)
    ok = bool(result.get("ok"))
    state_missing = bool(result.get("state_missing"))
    issues: list[dict[str, object]] = []
    if state_missing:
        issues.append(
            {
                "code": "run.state_missing",
                "message": "run state is missing; showing git-managed artifact truth only",
            }
        )
    for item in result.get("issues", []):
        if isinstance(item, str) and item.strip():
            issues.append({"code": "reconcile.issue", "message": item.strip()})

    payload = _result_payload(
        command="reconcile",
        ok=ok,
        status="succeeded" if ok else "failed",
        run_id=run_id.strip(),
        exit_code=0 if ok else 1,
        issues=issues,
        data=result,
    )
    if mode == "json":
        _emit_payload(mode, payload, title="Reconcile Result")
        if not ok:
            raise typer.Exit(code=1)
        return

    summary = Table(title=f"Reconcile Summary for {run_id.strip()}")
    summary.add_column("Field")
    summary.add_column("Value")
    summary.add_row("Status", str(result.get("status") or "-"))
    summary.add_row("Applied", "yes" if result.get("applied") else "no")
    summary.add_row("Checkpoint", str(result.get("checkpoint_status") or "-"))
    summary.add_row("Plan", str(result.get("plan_path") or "-"))
    summary.add_row(
        "Retained Work",
        str(len(result.get("retained_work", [])))
        if isinstance(result.get("retained_work"), list)
        else "0",
    )
    inventory = (
        result.get("inventory", {}) if isinstance(result.get("inventory"), dict) else {}
    )
    summary.add_row(
        "Managed Worktrees",
        str(len(inventory.get("worktrees", [])))
        if isinstance(inventory.get("worktrees"), list)
        else "0",
    )
    summary.add_row(
        "Managed Branches",
        str(len(inventory.get("branches", [])))
        if isinstance(inventory.get("branches"), list)
        else "0",
    )
    console.print(summary)

    nodes = result.get("nodes", []) if isinstance(result.get("nodes"), list) else []
    if nodes:
        table = Table(title="Node Truth")
        table.add_column("Node")
        table.add_column("Persisted")
        table.add_column("Derived")
        table.add_column("Commit")
        table.add_column("Retained")
        for row in nodes:
            if not isinstance(row, dict):
                continue
            table.add_row(
                str(row.get("node_id") or "-"),
                str(row.get("persisted_status") or "-"),
                str(row.get("derived_status") or "-"),
                str(row.get("commit") or "")[:12] or "-",
                "yes" if row.get("retained") else "no",
            )
        console.print(table)

    if not ok:
        raise typer.Exit(code=1)
