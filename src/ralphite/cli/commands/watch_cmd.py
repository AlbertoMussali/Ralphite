from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..core import _normalize_output, _orchestrator, _print_run_stream, console


def _resolve_watch_run_id(orch, run_id: str | None) -> str | None:  # type: ignore[no-untyped-def]
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    rows = orch.list_history(limit=1)
    if not rows:
        return None
    latest = rows[0]
    candidate = str(getattr(latest, "id", "") or "").strip()
    return candidate or None


def watch_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str | None, typer.Option(help="Run id to watch")] = None,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: stream")
    ] = "stream",
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show extra event guidance")
    ] = False,
) -> None:
    """Tail events for an existing run."""
    orch = _orchestrator(workspace)
    output_mode = _normalize_output(output)
    if output_mode != "stream":
        raise typer.BadParameter("watch currently supports only --output stream")

    target_run_id = _resolve_watch_run_id(orch, run_id)
    if target_run_id is None:
        console.print("No runs found to watch.")
        console.print(
            "Start a run with `uv run ralphite run --workspace . --yes --output stream`."
        )
        raise typer.Exit(code=1)

    run = orch.get_run(target_run_id)
    if run is None:
        console.print(f"Run not found: {target_run_id}")
        raise typer.Exit(code=1)

    console.print(f"Watching run: [bold]{target_run_id}[/bold]")
    _print_run_stream(orch, target_run_id, verbose=verbose)
