from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree
import yaml
from ralphite.schemas import CliOutputEnvelopeV1

from ralphite.engine import (
    LocalOrchestrator,
    make_bootstrap_plan,
    present_event,
    present_run_status,
    validate_plan_content,
)

CLI_OUTPUT_SCHEMA_VERSION = "cli-output.v1"
console = Console()


def _orchestrator(workspace: Path, *, bootstrap: bool = True) -> LocalOrchestrator:
    return LocalOrchestrator(workspace.expanduser().resolve(), bootstrap=bootstrap)


def _resolve_plan_ref(orch: LocalOrchestrator, plan: str | None) -> Path:
    if plan:
        candidate = Path(plan)
        search = [candidate]
        if not candidate.is_absolute():
            search.extend(
                [orch.workspace_root / candidate, orch.paths["plans"] / candidate]
            )
        for item in search:
            if item.exists() and item.is_file():
                return item.resolve()
        raise FileNotFoundError(f"plan not found: {plan}")
    plans = orch.list_plans()
    if not plans:
        raise FileNotFoundError("no plans found in .ralphite/plans")
    return plans[0].resolve()


def _result_payload(
    *,
    command: str,
    ok: bool,
    status: str,
    run_id: str | None = None,
    exit_code: int = 0,
    issues: list[dict[str, Any]] | None = None,
    next_actions: list[str] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    envelope = CliOutputEnvelopeV1(
        schema_version=CLI_OUTPUT_SCHEMA_VERSION,
        command=command,
        ok=ok,
        status=status,
        run_id=run_id,
        exit_code=exit_code,
        issues=issues or [],
        next_actions=next_actions or [],
        data=data or {},
    )
    return envelope.model_dump(mode="json")


def _normalize_output(output: str, json_mode: bool = False) -> str:
    if json_mode:
        return "json"
    normalized = (output or "").strip().lower()
    if normalized in {"json", "table", "stream"}:
        return normalized
    return "table"


def _repo_root() -> Path:
    override = os.getenv("RALPHITE_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:  # noqa: BLE001
        pass
    return Path.cwd().resolve()


def _parse_csv_items(raw: str | None, *, default: list[str]) -> list[str]:
    if raw is None:
        return list(default)
    items = [item.strip() for item in str(raw).split(",") if item.strip()]
    return items or list(default)


def _find_first_valid_plan(orch: LocalOrchestrator) -> Path | None:
    for plan_path in orch.list_plans():
        valid, _issues, _summary = validate_plan_content(
            plan_path.read_text(encoding="utf-8"),
            workspace_root=orch.workspace_root,
            plan_path=str(plan_path),
        )
        if valid:
            return plan_path
    return None


def _bootstrap_plan_file(
    orch: LocalOrchestrator,
    *,
    template: str,
    goal: str | None,
    plan_id: str | None,
    name: str | None,
    lanes: list[str] | None = None,
    loop_unit: str = "per_task",
) -> Path:
    normalized_id = (plan_id or "starter_loop").strip() or "starter_loop"
    normalized_name = (name or "Starter Loop").strip() or "Starter Loop"
    plan_data = make_bootstrap_plan(
        template=template,
        plan_id=normalized_id,
        name=normalized_name,
        goal=goal,
        branched_lanes=lanes or ["lane_a", "lane_b"],
        blue_red_loop_unit=loop_unit,
    )
    base_path = orch.paths["plans"] / f"{normalized_id}.yaml"
    target = (
        base_path
        if not base_path.exists()
        else orch.paths["plans"] / f"{normalized_id}.{int(time.time())}.yaml"
    )
    target.write_text(
        yaml.safe_dump(plan_data, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    return target


def _print_run_stream(
    orch: LocalOrchestrator, run_id: str, *, verbose: bool = False
) -> None:
    console.print(f"\n[bold]Streaming run {run_id}[/bold]")
    for event in orch.stream_events(run_id):
        level = str(event.get("level", "info"))
        info = present_event(str(event.get("event", "")))
        color = "green" if level == "info" else "yellow" if level == "warn" else "red"
        message = str(event.get("message", ""))
        console.print(f"[{color}]{info.title}[/{color}] {message}")
        if verbose or level in {"warn", "error"}:
            console.print(f"  next: {info.next_action}")
        if event.get("event") == "RUN_DONE":
            break

    orch.wait_for_run(run_id, timeout=2.0)
    run = orch.get_run(run_id)
    if run and run.artifacts:
        console.print()
        tree = Tree("[bold]Artifacts[/bold]")
        for artifact in run.artifacts:
            tree.add(f"{artifact['id']}: {artifact['path']}")
        console.print(tree)


def _emit_payload(
    output: str, payload: dict[str, Any], *, title: str | None = None
) -> None:
    if output == "json":
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        sys.stdout.flush()
        return

    lines: list[str] = []
    status = present_run_status(str(payload.get("status", "")))
    status_color = "green" if status.severity == "info" else ("yellow" if status.severity == "warn" else "red")
    lines.append(f"Status: [{status_color}]{status.label}[/{status_color}]")
    
    run_id = payload.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        lines.append(f"Run ID: {run_id}")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if isinstance(data, dict):
        plan_path = data.get("plan_path")
        if isinstance(plan_path, str) and plan_path.strip():
            lines.append(f"Plan: {plan_path}")
            
    content = "\n".join(lines)
    
    artifacts = data.get("artifacts") if isinstance(data, dict) and isinstance(data.get("artifacts"), list) else []
    
    renderables = [Text.from_markup(content)]
    
    if artifacts:
        tree = Tree(f"[bold]Artifacts ({len(artifacts)})[/bold]")
        shown = 0
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not isinstance(path, str) or not path:
                continue
            shown += 1
            tree.add(path)
            if shown >= 5:
                break
        if len(artifacts) > shown:
            tree.add(f"... ({len(artifacts) - shown} more)")
        renderables.append(Text(""))
        renderables.append(tree)

    issues = payload.get("issues", [])
    if isinstance(issues, list) and issues:
        renderables.append(Text("\nIssues:", style="bold red"))
        for issue in issues:
            if isinstance(issue, dict):
                renderables.append(Text(f"- {issue.get('code')}: {issue.get('message')}"))
            else:
                renderables.append(Text(f"- {issue}"))

    actions = payload.get("next_actions", [])
    if isinstance(actions, list) and actions:
        renderables.append(Text("\nNext actions:", style="bold blue"))
        for item in actions:
            renderables.append(Text(f"- {item}"))

    panel = Panel(
        Group(*renderables),
        title=f"[bold]{title}[/bold]" if title else "[bold]Result Payload[/bold]",
        expand=False,
        border_style="blue"
    )
    console.print(panel)
