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
    GitWorktreeManager,
    LocalOrchestrator,
    make_bootstrap_plan,
    present_event,
    present_recovery_mode,
    present_run_status,
    validate_plan_content,
)
from ralphite.engine.taxonomy import classify_failure

CLI_OUTPUT_SCHEMA_VERSION = "cli-output.v1"
console = Console()


def _safe_console_print(*args: Any, **kwargs: Any) -> None:
    try:
        console.print(*args, **kwargs)
    except UnicodeEncodeError:
        plain = " ".join(str(item) for item in args)
        sys.stdout.write(plain.encode("ascii", errors="replace").decode("ascii") + "\n")
        sys.stdout.flush()


def _dedupe_strings(items: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def _summarize_capability_group(kind: str, entries: list[str]) -> dict[str, object]:
    normalized = _dedupe_strings(entries)
    singular = "tool" if kind == "tool" else "MCP server"
    plural = "tools" if kind == "tool" else "MCP servers"
    wildcard = f"{kind}:*"
    named = [item.split(":", 1)[1] for item in normalized if ":" in item]

    if not normalized:
        summary = f"No {plural} requested by the selected plan."
        scope = "none"
    elif wildcard in normalized:
        summary = f"All {plural} declared by the selected plan."
        scope = "all"
    elif len(named) == 1:
        summary = f"1 {singular}: {named[0]}"
        scope = "selected"
    else:
        preview = ", ".join(named[:3])
        if len(named) > 3:
            preview = f"{preview}, +{len(named) - 3} more"
        summary = f"{len(named)} {plural}: {preview}"
        scope = "selected"

    return {
        "entries": normalized,
        "count": len(normalized),
        "scope": scope,
        "summary": summary,
    }


def _build_capability_summary(
    requirements: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    payload = requirements or {"tools": [], "mcps": []}
    tools = payload.get("tools", []) if isinstance(payload, dict) else []
    mcps = payload.get("mcps", []) if isinstance(payload, dict) else []
    tool_summary = _summarize_capability_group(
        "tool",
        tools if isinstance(tools, list) else [],
    )
    mcp_summary = _summarize_capability_group(
        "mcp",
        mcps if isinstance(mcps, list) else [],
    )
    return {
        "tools": tool_summary,
        "mcps": mcp_summary,
        "approval_scope": (
            "Approval covers the tool and MCP access declared by the selected plan "
            "for this run."
        ),
    }


def _build_execution_summary(
    *,
    plan_path: str,
    backend: str,
    model: str,
    reasoning_effort: str,
    capabilities: dict[str, object],
    duration_seconds: float | int,
    artifacts_count: int,
) -> dict[str, object]:
    return {
        "plan_path": plan_path,
        "backend": backend,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "capabilities": capabilities,
        "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
        "artifacts_count": max(0, int(artifacts_count)),
    }


def _print_preflight_summary(
    *,
    title: str,
    plan_path: str,
    backend: str,
    model: str,
    reasoning_effort: str,
    capabilities: dict[str, object],
) -> None:
    tool_summary = capabilities.get("tools", {})
    mcp_summary = capabilities.get("mcps", {})
    lines = [
        f"Plan: {plan_path or 'No plan selected'}",
        f"Backend: {backend}",
        f"Model: {model}",
        f"Reasoning effort: {reasoning_effort}",
        str(tool_summary.get("summary", "No tools requested.")),
        str(mcp_summary.get("summary", "No MCP servers requested.")),
        str(
            capabilities.get(
                "approval_scope",
                "Approval covers the capabilities declared by the plan.",
            )
        ),
    ]
    _safe_console_print(
        Panel(
            "\n".join(lines),
            title=f"[bold]{title}[/bold]",
            border_style="blue",
            expand=False,
            padding=(0, 1),
        )
    )


def _find_final_report_path(artifacts: list[dict[str, Any]]) -> Path | None:
    for item in artifacts:
        if (
            isinstance(item, dict)
            and item.get("id") == "final_report"
            and isinstance(item.get("path"), str)
        ):
            return Path(item["path"])
    return None


def _read_final_report_preview(path: Path | None) -> tuple[Path | None, list[str]]:
    if path is None or not path.exists():
        return path, []
    try:
        report_lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return path, []
    preview = [line.strip() for line in report_lines if line.strip()][:3]
    return path, preview


def _render_final_report_preview(
    renderables: list[Any], artifacts: list[dict[str, Any]]
) -> None:
    final_report_path, preview = _read_final_report_preview(
        _find_final_report_path(artifacts)
    )
    if final_report_path is None or not final_report_path.exists():
        return
    renderables.append(Text(""))
    renderables.append(Text("Final Report:", style="bold green"))
    renderables.append(Text(str(final_report_path)))
    if preview:
        renderables.append(Text(""))
        for line in preview:
            renderables.append(Text(f"│ {line}", style="italic dim"))


def _display_recovery_mode(data: dict[str, Any]) -> str | None:
    label = data.get("recovery_mode_label")
    if isinstance(label, str) and label.strip():
        return label
    raw = data.get("recovery_mode")
    if not isinstance(raw, str) or not raw.strip():
        return None
    display = present_recovery_mode(raw)
    return display if display != "Not Selected" else raw


def _display_recommended_recovery_mode(data: dict[str, Any]) -> str | None:
    label = data.get("recommended_recovery_mode_label")
    if isinstance(label, str) and label.strip():
        return label
    raw = data.get("recommended_recovery_mode")
    if not isinstance(raw, str) or not raw.strip():
        return None
    display = present_recovery_mode(raw)
    return display if display != "Not Selected" else raw


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


def _git_required_payload(
    *,
    command: str,
    workspace: Path,
    title: str,
    output: str,
    run_id: str | None = None,
    data: dict[str, Any] | None = None,
    git_status: dict[str, Any] | None = None,
    exit_code: int = 1,
) -> None:
    advice = classify_failure("git_required")
    status = (
        git_status
        or GitWorktreeManager(workspace.expanduser().resolve(), "cli").runtime_status()
    )
    detail = str(status.get("detail") or advice.message).strip() or advice.message
    remediation = str(status.get("remediation") or "").strip()
    payload = _result_payload(
        command=command,
        ok=False,
        status="failed",
        run_id=run_id,
        exit_code=exit_code,
        issues=[{"code": "git.required", "message": detail}],
        next_actions=_dedupe_strings(
            [remediation, advice.next_action, advice.command_hint]
        ),
        data={"git": status, **(data or {})},
    )
    _emit_payload(output, payload, title=title)


def _run_start_blocked_payload(
    *,
    command: str,
    title: str,
    output: str,
    preflight: dict[str, Any],
    data: dict[str, Any] | None = None,
    exit_code: int = 1,
) -> None:
    advice = classify_failure("stale_recovery_state_present")
    detail = str(preflight.get("detail") or advice.message).strip() or advice.message
    payload = _result_payload(
        command=command,
        ok=False,
        status="failed",
        exit_code=exit_code,
        issues=[
            {
                "code": str(preflight.get("reason") or advice.code),
                "message": detail,
            }
        ],
        next_actions=_dedupe_strings(
            list(preflight.get("next_commands") or [])
            + [advice.next_action, advice.command_hint]
        ),
        data={"run_start_preflight": preflight, **(data or {})},
    )
    _emit_payload(output, payload, title=title)


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
    _safe_console_print(f"\n[bold]Streaming run {run_id}[/bold]")
    for event in orch.stream_events(run_id):
        level = str(event.get("level", "info"))
        info = present_event(str(event.get("event", "")))
        color = "green" if level == "info" else "yellow" if level == "warn" else "red"
        message = str(event.get("message", ""))
        _safe_console_print(f"[{color}]{info.title:20}[/{color}] {message}")
        if verbose or level in {"warn", "error"}:
            _safe_console_print(f"  [dim]next: {info.next_action}[/dim]")
        if event.get("event") == "RUN_DONE":
            break

    orch.wait_for_run(run_id, timeout=2.0)
    run = orch.get_run(run_id)
    if run and run.artifacts:
        _safe_console_print()
        renderables: list[Any] = []
        _render_final_report_preview(
            renderables,
            [item for item in run.artifacts if isinstance(item, dict)],
        )
        if renderables:
            _safe_console_print(Group(*renderables))
            _safe_console_print()
        tree = Tree("[bold]Artifacts[/bold]")
        for artifact in run.artifacts:
            tree.add(f"{artifact['id']}: {artifact['path']}")
        _safe_console_print(tree)


def _emit_payload(
    output: str, payload: dict[str, Any], *, title: str | None = None
) -> None:
    if output == "json":
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        sys.stdout.flush()
        return

    lines: list[str] = []
    status = present_run_status(str(payload.get("status", "")))
    status_color = (
        "green"
        if status.severity == "info"
        else "yellow"
        if status.severity == "warn"
        else "red"
    )
    lines.append(f"Status: [{status_color}]{status.label}[/{status_color}]")

    run_id = payload.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        lines.append(f"Run ID: {run_id}")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if isinstance(data, dict):
        plan_path = data.get("plan_path")
        if isinstance(plan_path, str) and plan_path.strip():
            lines.append(f"Plan: {plan_path}")

    execution_summary = (
        data.get("execution_summary")
        if isinstance(data, dict) and isinstance(data.get("execution_summary"), dict)
        else {}
    )
    if execution_summary:
        backend = execution_summary.get("backend")
        model = execution_summary.get("model")
        reasoning_effort = execution_summary.get("reasoning_effort")
        duration_seconds = execution_summary.get("duration_seconds")
        artifacts_count = execution_summary.get("artifacts_count")
        if isinstance(backend, str) and backend.strip():
            lines.append(f"Backend: {backend}")
        if isinstance(model, str) and model.strip():
            lines.append(f"Model: {model}")
        if isinstance(reasoning_effort, str) and reasoning_effort.strip():
            lines.append(f"Reasoning effort: {reasoning_effort}")
        if isinstance(duration_seconds, (int, float)):
            lines.append(f"Duration: {round(float(duration_seconds), 3)}s")
        if isinstance(artifacts_count, int):
            lines.append(f"Artifacts found: {artifacts_count}")

    content = "\n".join(lines)

    artifacts = (
        data.get("artifacts")
        if isinstance(data, dict) and isinstance(data.get("artifacts"), list)
        else []
    )

    renderables: list[Any] = [Text.from_markup(content)]

    if artifacts:
        _render_final_report_preview(
            renderables,
            [item for item in artifacts if isinstance(item, dict)],
        )

    capabilities = execution_summary.get("capabilities", {})
    if isinstance(capabilities, dict):
        tool_summary = capabilities.get("tools", {})
        mcp_summary = capabilities.get("mcps", {})
        capability_lines: list[str] = []
        if isinstance(tool_summary, dict) and tool_summary.get("summary"):
            capability_lines.append(str(tool_summary["summary"]))
        if isinstance(mcp_summary, dict) and mcp_summary.get("summary"):
            capability_lines.append(str(mcp_summary["summary"]))
        if capability_lines:
            renderables.append(Text("\nCapability scope:", style="bold"))
            for line in capability_lines:
                renderables.append(Text(f"- {line}"))

    issues = payload.get("issues", [])
    if isinstance(issues, list) and issues:
        top_issue = issues[0]
        if isinstance(top_issue, dict):
            renderables.append(Text(""))
            renderables.append(Text("Top issue:", style="bold red"))
            renderables.append(
                Text(
                    f"{top_issue.get('code')}: {top_issue.get('message')}",
                    style="red",
                )
            )

    actions = payload.get("next_actions", [])
    if isinstance(actions, list) and actions:
        next_action = actions[0]
        renderables.append(Text(""))
        renderables.append(Text("Next action:", style="bold blue"))
        renderables.append(Text(str(next_action)))

    if isinstance(data, dict):
        primary_failure_reason = data.get("primary_failure_reason")
        if isinstance(primary_failure_reason, str) and primary_failure_reason.strip():
            renderables.append(Text(""))
            renderables.append(Text("Failure signal:", style="bold"))
            renderables.append(Text(primary_failure_reason))

        git_warning = data.get("git_warning")
        if isinstance(git_warning, str) and git_warning.strip():
            renderables.append(Text(""))
            renderables.append(Text("Workspace warning:", style="bold yellow"))
            renderables.append(Text(git_warning))

        recovery_mode = _display_recovery_mode(data)
        if isinstance(recovery_mode, str) and recovery_mode.strip():
            renderables.append(Text(""))
            renderables.append(Text("Recovery mode:", style="bold"))
            renderables.append(Text(recovery_mode))

        recommended_mode = _display_recommended_recovery_mode(data)
        if isinstance(recommended_mode, str) and recommended_mode.strip():
            renderables.append(Text(""))
            renderables.append(Text("Recommended recovery mode:", style="bold"))
            renderables.append(Text(recommended_mode))
            recommended_reason = data.get("recommended_recovery_reason")
            if isinstance(recommended_reason, str) and recommended_reason.strip():
                renderables.append(Text(recommended_reason))

    run_status_raw = str(payload.get("status", "")).lower()
    renderables.append(Text(""))
    renderables.append(Text("Next context:", style="bold blue"))
    if run_status_raw == "succeeded":
        renderables.append(
            Text("Review final_report.md and share results / next steps.")
        )
    elif (
        run_status_raw == "paused"
        and isinstance(data, dict)
        and (_display_recommended_recovery_mode(data) or _display_recovery_mode(data))
    ):
        rec_mode_display = _display_recommended_recovery_mode(
            data
        ) or _display_recovery_mode(data)
        renderables.append(
            Text(f"Run is paused. Continue with recovery mode: {rec_mode_display}")
        )
    elif (
        run_status_raw == "paused_recovery_required"
        and isinstance(data, dict)
        and (_display_recommended_recovery_mode(data) or _display_recovery_mode(data))
    ):
        rec_mode_display = _display_recommended_recovery_mode(
            data
        ) or _display_recovery_mode(data)
        renderables.append(
            Text(f"Recovery is blocked. Recommended mode: {rec_mode_display}")
        )
    else:
        # Failed or other unexpected statuses
        if isinstance(run_id, str):
            renderables.append(
                Text(
                    f"Use `ralphite history` to inspect, or run `ralphite recover --workspace .` or `ralphite replay {run_id}`."
                )
            )
        else:
            renderables.append(
                Text(
                    "Use `ralphite history` to inspect, or run `ralphite recover --workspace .`"
                )
            )

    if isinstance(data, dict):
        preflight = data.get("preflight")
        if isinstance(preflight, dict):
            blockers = preflight.get("blocking_reasons", [])
            if isinstance(blockers, list) and blockers:
                renderables.append(Text(""))
                renderables.append(Text("Recovery blockers:", style="bold yellow"))
                for item in blockers:
                    renderables.append(Text(f"- {item}"))
            next_commands = preflight.get("next_commands", [])
            if isinstance(next_commands, list) and next_commands:
                renderables.append(Text(""))
                renderables.append(Text("Suggested commands:", style="bold blue"))
                for item in next_commands:
                    renderables.append(Text(f"- {item}"))

    if artifacts:
        tree = Tree(f"[bold]Artifacts ({len(artifacts)})[/bold]")
        shown = 0
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("id") or "artifact")
            path = item.get("path")
            if not isinstance(path, str) or not path:
                continue
            shown += 1
            tree.add(f"{artifact_id}: {path}")
            if shown >= 5:
                break
        if len(artifacts) > shown:
            tree.add(f"... ({len(artifacts) - shown} more)")
        renderables.append(Text(""))
        renderables.append(tree)

    panel = Panel(
        Group(*renderables),
        title=f"[bold]{title}[/bold]" if title else "[bold]Result Payload[/bold]",
        expand=False,
        border_style="blue",
        padding=(0, 1),
    )
    _safe_console_print(panel)
