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


def _resolve_salvage_run_id(orch, run_id: str | None) -> str | None:  # noqa: ANN001
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    for run in orch.list_history(limit=100):
        retained = (
            run.metadata.get("retained_work", [])
            if isinstance(run.metadata.get("retained_work"), list)
            else []
        )
        if isinstance(retained, list) and retained:
            return run.id
    return None


def _salvage_rows_from_inventory(
    inventory: dict[str, object],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in inventory.get("worktrees", []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "scope": "worktree",
                "phase": "",
                "node_id": "",
                "reason": "orphan_managed_artifact",
                "salvage_class": "orphan_managed_artifact",
                "branch": str(item.get("branch") or ""),
                "commit": str(item.get("commit") or ""),
                "worktree_path": str(item.get("path") or ""),
                "worktree_exists": bool(item.get("exists")),
                "branch_exists": bool(str(item.get("branch") or "").strip()),
                "prunable": str(item.get("prunable") or ""),
            }
        )
    seen_branches = {
        str(item.get("branch") or "")
        for item in inventory.get("worktrees", [])
        if isinstance(item, dict)
    }
    for item in inventory.get("branches", []):
        if not isinstance(item, dict):
            continue
        branch = str(item.get("branch") or "")
        if branch in seen_branches:
            continue
        rows.append(
            {
                "scope": "branch",
                "phase": "",
                "node_id": "",
                "reason": "orphan_managed_artifact",
                "salvage_class": "orphan_managed_artifact",
                "branch": branch,
                "commit": str(item.get("commit") or ""),
                "worktree_path": "",
                "worktree_exists": False,
                "branch_exists": bool(item.get("exists")),
                "prunable": "",
            }
        )
    return rows


def salvage_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str | None, typer.Option(help="Run ID to inspect")] = None,
    output: Annotated[
        str, typer.Option("--output", help="Output mode: table | json")
    ] = "table",
) -> None:
    """Inspect preserved work retained from a failed or interrupted run."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    selected_run_id = _resolve_salvage_run_id(orch, run_id)
    if not selected_run_id:
        payload = _result_payload(
            command="salvage",
            ok=False,
            status="failed",
            exit_code=1,
            issues=[
                {
                    "code": "run.not_found",
                    "message": "no run with retained work was found",
                }
            ],
            next_actions=[
                "Run Ralphite again or pass --run-id for a known failed/interrupted run."
            ],
            data={"rows": []},
        )
        _emit_payload(mode, payload, title="Salvage Result")
        raise typer.Exit(code=1)

    run = orch.get_run(selected_run_id)
    rows: list[dict[str, object]]
    plan_path = ""
    cleanup_decision: dict[str, object] = {}
    artifacts: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    next_actions = [
        "Inspect the retained branch/worktree details before discarding preserved work.",
        "Use `ralphite cleanup --discard-preserved --yes` only after verifying the retained work is no longer needed.",
    ]
    if run is not None:
        retained = (
            run.metadata.get("retained_work", [])
            if isinstance(run.metadata.get("retained_work"), list)
            else []
        )
        rows = [
            {
                "scope": str(item.get("scope") or ""),
                "phase": str(item.get("phase") or ""),
                "node_id": str(item.get("node_id") or ""),
                "reason": str(item.get("reason") or ""),
                "salvage_class": str(item.get("salvage_class") or ""),
                "branch": str(item.get("branch") or ""),
                "commit": str(item.get("commit") or ""),
                "worktree_path": str(item.get("worktree_path") or ""),
                "worktree_exists": bool(item.get("worktree_exists")),
                "branch_exists": bool(item.get("branch_exists")),
            }
            for item in retained
            if isinstance(item, dict)
        ]
        plan_path = run.plan_path
        cleanup_decision = (
            run.metadata.get("cleanup_decision", {})
            if isinstance(run.metadata.get("cleanup_decision"), dict)
            else {}
        )
        artifacts = [item for item in run.artifacts if isinstance(item, dict)]
    else:
        manager = GitWorktreeManager(orch.workspace_root, selected_run_id)
        inventory = manager.managed_artifact_inventory(selected_run_id)
        rows = _salvage_rows_from_inventory(inventory)
        if not rows:
            payload = _result_payload(
                command="salvage",
                ok=False,
                status="failed",
                run_id=selected_run_id,
                exit_code=1,
                issues=[
                    {
                        "code": "run.not_found",
                        "message": "run state and managed artifacts were not found for salvage",
                    }
                ],
                data={"rows": []},
            )
            _emit_payload(mode, payload, title="Salvage Result")
            raise typer.Exit(code=1)
        issues.append(
            {
                "code": "run.state_missing",
                "message": "run state is missing; showing managed git artifacts discovered from repository metadata",
            }
        )
        next_actions = [
            "If these preserved artifacts are no longer needed, run `ralphite cleanup --discard-preserved --yes` with the same run id.",
            "If you expected a richer report, inspect the repository history for the missing `.ralphite/runs/<run-id>` state.",
        ]

    payload = _result_payload(
        command="salvage",
        ok=True,
        status="succeeded",
        run_id=selected_run_id,
        exit_code=0,
        issues=issues,
        next_actions=next_actions,
        data={
            "plan_path": plan_path,
            "rows": rows,
            "retained_count": len(rows),
            "cleanup_decision": cleanup_decision,
            "artifacts": artifacts,
            "state_missing": run is None,
        },
    )
    if mode == "json":
        _emit_payload(mode, payload, title="Salvage Result")
        return

    table = Table(title=f"Preserved Work for {selected_run_id}")
    table.add_column("Scope")
    table.add_column("Phase")
    table.add_column("Node")
    table.add_column("Reason")
    table.add_column("Class")
    table.add_column("Branch")
    table.add_column("Commit")
    table.add_column("Worktree")
    table.add_column("Present")
    for row in rows:
        table.add_row(
            row["scope"],
            row["phase"] or "-",
            row["node_id"] or "-",
            row["reason"] or "-",
            row["salvage_class"] or "-",
            row["branch"] or "-",
            row["commit"][:12] if row["commit"] else "-",
            row["worktree_path"] or "-",
            "yes" if row["worktree_exists"] or row["branch_exists"] else "no",
        )
    console.print(table)
