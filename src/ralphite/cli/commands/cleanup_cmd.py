from __future__ import annotations

from pathlib import Path
from typing import Annotated

from rich.table import Table
import typer

from ralphite.engine.git_worktree import GitWorktreeManager

from ..core import (
    _emit_payload,
    _normalize_output,
    _orchestrator,
    _result_payload,
    console,
)


def _resolve_cleanup_run_id(orch, run_id: str | None) -> str | None:  # noqa: ANN001
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    for run in orch.list_history(limit=100):
        retained = (
            run.metadata.get("retained_work", [])
            if isinstance(run.metadata.get("retained_work"), list)
            else []
        )
        if retained:
            return run.id
    return None


def cleanup_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str | None, typer.Option(help="Run ID to clean up")] = None,
    discard_preserved: Annotated[
        bool,
        typer.Option(
            "--discard-preserved",
            help="Also delete worktrees/branches marked preserved for salvage",
        ),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip confirmation for destructive cleanup")
    ] = False,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: table | json")
    ] = "table",
) -> None:
    """Clean Ralphite-managed git artifacts for a completed run."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    selected_run_id = _resolve_cleanup_run_id(orch, run_id)
    if not selected_run_id:
        payload = _result_payload(
            command="cleanup",
            ok=False,
            status="failed",
            exit_code=1,
            issues=[
                {
                    "code": "run.not_found",
                    "message": "no cleanup candidate run was found",
                }
            ],
            next_actions=[
                "Pass --run-id for a known completed run or inspect `ralphite history`."
            ],
            data={},
        )
        _emit_payload(mode, payload, title="Cleanup Result")
        raise typer.Exit(code=1)

    state = orch.run_store.load_state(selected_run_id)
    manager = GitWorktreeManager(orch.workspace_root, selected_run_id)
    if state is None:
        inventory = manager.managed_artifact_inventory(selected_run_id)
        has_artifacts = bool(inventory.get("branches") or inventory.get("worktrees"))
        if not has_artifacts:
            payload = _result_payload(
                command="cleanup",
                ok=False,
                status="failed",
                run_id=selected_run_id,
                exit_code=1,
                issues=[
                    {
                        "code": "run.not_found",
                        "message": "run state and managed artifacts were not found for cleanup",
                    }
                ],
                data={},
            )
            _emit_payload(mode, payload, title="Cleanup Result")
            raise typer.Exit(code=1)
        if not discard_preserved:
            payload = _result_payload(
                command="cleanup",
                ok=False,
                status="failed",
                run_id=selected_run_id,
                exit_code=1,
                issues=[
                    {
                        "code": "cleanup.state_missing_requires_discard",
                        "message": "run state is missing; rerun cleanup with --discard-preserved --yes to remove orphaned managed artifacts",
                    }
                ],
                next_actions=[
                    "Use `ralphite salvage` first if you want to inspect the orphaned artifacts.",
                    "Then rerun `ralphite cleanup --discard-preserved --yes` with the same run id.",
                ],
                data={"state_missing": True},
            )
            _emit_payload(mode, payload, title="Cleanup Result")
            raise typer.Exit(code=1)
        notes = manager.cleanup_orphaned_run_artifacts(selected_run_id)
        payload = _result_payload(
            command="cleanup",
            ok=True,
            status="succeeded",
            run_id=selected_run_id,
            exit_code=0,
            issues=[
                {
                    "code": "run.state_missing",
                    "message": "run state was missing; cleaned orphaned managed artifacts discovered from repository metadata",
                }
            ],
            next_actions=[
                "Retry your Ralphite run now that the orphaned managed artifacts have been removed."
            ],
            data={
                "plan_path": "",
                "notes": notes,
                "retained_count": 0,
                "discard_preserved": True,
                "artifacts": [],
                "state_missing": True,
            },
        )
        _emit_payload(mode, payload, title="Cleanup Result")
        return

    run = state.run
    if run.status in {
        "running",
        "paused",
        "paused_recovery_required",
        "recovering",
        "checkpointing",
    }:
        payload = _result_payload(
            command="cleanup",
            ok=False,
            status="failed",
            run_id=selected_run_id,
            exit_code=1,
            issues=[
                {"code": "run.not_found", "message": "run state not found for cleanup"}
            ],
            data={},
        )
        _emit_payload(mode, payload, title="Cleanup Result")
        raise typer.Exit(code=1)

    if discard_preserved and not yes:
        confirmed = typer.confirm(
            "Delete preserved worktrees and branches for this run?", default=False
        )
        if not confirmed:
            raise typer.Exit(code=1)

    git_state = (
        run.metadata.get("git_state", {})
        if isinstance(run.metadata.get("git_state"), dict)
        else {}
    )
    notes = manager.cleanup_all(git_state, discard_preserved=discard_preserved)
    reconciliation = manager.reconcile_state(git_state)
    run.metadata["git_reconciliation"] = reconciliation
    run.metadata["retained_work"] = list(git_state.get("retained_work", []))
    run.metadata["cleanup_decision"] = {
        "status": run.status,
        "cleanup_allowed": True,
        "mode": "explicit_discard" if discard_preserved else "explicit_safe_cleanup",
        "discard_preserved": discard_preserved,
        "notes": notes,
    }
    orch.run_store.write_state(state)
    orch.history.upsert(run)
    orch._write_artifacts(run)  # noqa: SLF001

    payload = _result_payload(
        command="cleanup",
        ok=True,
        status="succeeded",
        run_id=run.id,
        exit_code=0,
        next_actions=[
            "Review the cleanup notes and retained work count before deleting anything else manually."
        ],
        data={
            "plan_path": run.plan_path,
            "notes": notes,
            "retained_count": len(run.metadata.get("retained_work", [])),
            "discard_preserved": discard_preserved,
            "artifacts": run.artifacts,
        },
    )
    if mode == "json":
        _emit_payload(mode, payload, title="Cleanup Result")
        return

    table = Table(title=f"Cleanup Notes for {run.id}")
    table.add_column("Note")
    if notes:
        for note in notes:
            table.add_row(str(note))
    else:
        table.add_row("No cleanup actions were required.")
    console.print(table)
