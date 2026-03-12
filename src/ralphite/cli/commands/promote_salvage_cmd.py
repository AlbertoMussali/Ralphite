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


def promote_salvage_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str, typer.Option(help="Run ID to promote from")] = "",
    node_id: Annotated[str, typer.Option(help="Worker node ID to promote")] = "",
    output: Annotated[
        str, typer.Option("--output", help="Output mode: table | json")
    ] = "table",
) -> None:
    """Promote retained committed worker work into the normal phase/base integration flow."""
    mode = _normalize_output(output)
    missing: list[dict[str, str]] = []
    if not run_id.strip():
        missing.append(
            {
                "code": "promote_salvage.run_id_required",
                "message": "--run-id is required",
            }
        )
    if not node_id.strip():
        missing.append(
            {
                "code": "promote_salvage.node_id_required",
                "message": "--node-id is required",
            }
        )
    if missing:
        payload = _result_payload(
            command="promote-salvage",
            ok=False,
            status="failed",
            exit_code=1,
            issues=missing,
        )
        _emit_payload(mode, payload, title="Promote Salvage")
        raise typer.Exit(code=1)

    orch = _orchestrator(workspace)
    ok, result = orch.promote_salvage(run_id.strip(), node_id.strip())
    payload = _result_payload(
        command="promote-salvage",
        ok=ok,
        status="succeeded" if ok else "failed",
        run_id=run_id.strip(),
        exit_code=0 if ok else 1,
        issues=(
            []
            if ok
            else [
                {
                    "code": str(result.get("reason") or "promote_salvage.failed"),
                    "message": str(
                        result.get("error") or "unable to promote retained work"
                    ),
                }
            ]
        ),
        data=result,
    )
    if mode == "json":
        _emit_payload(mode, payload, title="Promote Salvage")
        if not ok:
            raise typer.Exit(code=1)
        return

    table = Table(title=f"Promote Salvage for {node_id.strip()}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Run", run_id.strip())
    table.add_row("Node", node_id.strip())
    if ok:
        table.add_row("Run Status", str(result.get("run_status") or "-"))
        table.add_row("Branch", str(result.get("branch") or "-"))
        table.add_row("Commit", str(result.get("commit") or "")[:12] or "-")
        table.add_row("Retained Remaining", str(result.get("retained_count") or 0))
    else:
        table.add_row("Reason", str(result.get("reason") or "-"))
        table.add_row("Error", str(result.get("error") or "-"))
    console.print(table)

    if not ok:
        raise typer.Exit(code=1)
