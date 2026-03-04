from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Annotated, Any

from rich.console import Console
from rich.table import Table
import typer
import yaml

from ralphite_engine import (
    LocalConfig,
    LocalOrchestrator,
    apply_fix,
    present_event,
    present_run_status,
    save_config,
    seed_starter_if_missing,
    suggest_fixes,
    validate_plan_content,
)
from ralphite_tui.tui.app_shell import AppShell

app = typer.Typer(help="Ralphite terminal-first orchestrator", no_args_is_help=True, add_completion=False)
console = Console()

RECOVER_EXIT_SUCCESS = 0
RECOVER_EXIT_NO_RECOVERABLE = 10
RECOVER_EXIT_UNRECOVERABLE = 11
RECOVER_EXIT_INVALID_INPUT = 12
RECOVER_EXIT_PREFLIGHT_FAILED = 13
RECOVER_EXIT_PENDING = 14
RECOVER_EXIT_TERMINAL_FAILURE = 15
RECOVER_EXIT_INTERNAL_ERROR = 16


def _orchestrator(workspace: Path) -> LocalOrchestrator:
    return LocalOrchestrator(workspace.expanduser().resolve())


def _resolve_plan_ref(orch: LocalOrchestrator, plan: str | None) -> Path:
    if plan:
        candidate = Path(plan)
        search = [candidate]
        if not candidate.is_absolute():
            search.extend([orch.workspace_root / candidate, orch.paths["plans"] / candidate])
        for item in search:
            if item.exists() and item.is_file():
                return item.resolve()
        raise FileNotFoundError(f"plan not found: {plan}")
    plans = orch.list_plans()
    if not plans:
        raise FileNotFoundError("no plans found in .ralphite/plans")
    return plans[0].resolve()


def _validate_all_plans(orch: LocalOrchestrator) -> tuple[bool, list[tuple[Path, list[dict], dict]]]:
    failures: list[tuple[Path, list[dict], dict]] = []
    for plan_path in orch.list_plans():
        content = plan_path.read_text(encoding="utf-8")
        valid, issues, summary = validate_plan_content(content, workspace_root=orch.workspace_root)
        if valid:
            continue
        failures.append((plan_path, issues, summary))
    return len(failures) == 0, failures


def _result_payload(
    *,
    ok: bool,
    status: str,
    run_id: str | None = None,
    exit_code: int = 0,
    issues: list[dict[str, Any]] | None = None,
    next_actions: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "run_id": run_id,
        "exit_code": exit_code,
        "issues": issues or [],
        "next_actions": next_actions or [],
    }
    if extra:
        payload.update(extra)
    return payload


def _normalize_output(output: str, json_mode: bool = False) -> str:
    if json_mode:
        return "json"
    normalized = (output or "").strip().lower()
    if normalized in {"json", "table", "stream"}:
        return normalized
    return "table"


def _doctor_snapshot(orch: LocalOrchestrator, include_fix_suggestions: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    required_cmds = ["python3", "uv", "git", "rg"]
    for cmd in required_cmds:
        found = shutil.which(cmd)
        status = "OK" if found else "MISSING"
        if not found:
            ok = False
        checks.append({"check": f"cmd:{cmd}", "status": status, "detail": found or "not in PATH"})

    cfg_path = orch.paths["config"]
    cfg_ok = cfg_path.exists()
    checks.append({"check": "config", "status": "OK" if cfg_ok else "MISSING", "detail": str(cfg_path)})
    if not cfg_ok:
        ok = False

    plans = orch.list_plans()
    checks.append({"check": "plans", "status": "OK" if plans else "MISSING", "detail": f"{len(plans)} plan file(s)"})
    if not plans:
        ok = False

    valid_plans, failures = _validate_all_plans(orch)
    checks.append(
        {
            "check": "plan-validation",
            "status": "OK" if valid_plans else "FAIL",
            "detail": "all plans valid" if valid_plans else f"{len(failures)} invalid",
        }
    )
    if not valid_plans:
        ok = False

    tasks_ok = True
    task_group_ok = True
    git_ready_ok = True
    for plan in plans:
        valid, _issues, summary = validate_plan_content(plan.read_text(encoding="utf-8"), workspace_root=orch.workspace_root)
        if not valid:
            continue
        task_status = str(summary.get("tasks_status", {}).get("status", "unknown"))
        if task_status not in {"ok", "issues"}:
            tasks_ok = False
        if summary.get("task_group_issues"):
            task_group_ok = False
        readiness = summary.get("recovery_readiness", {})
        if str(readiness.get("status")) not in {"ready", "dirty", "degraded"}:
            git_ready_ok = False

    checks.append({"check": "tasks", "status": "OK" if tasks_ok else "FAIL", "detail": "embedded YAML tasks parseable"})
    if not tasks_ok:
        ok = False

    checks.append(
        {
            "check": "task-groups",
            "status": "OK" if task_group_ok else "FAIL",
            "detail": "parallel_group definitions are consistent",
        }
    )
    if not task_group_ok:
        ok = False

    checks.append(
        {
            "check": "recovery-readiness",
            "status": "OK" if git_ready_ok else "FAIL",
            "detail": "git/worktree readiness computed",
        }
    )
    if not git_ready_ok:
        ok = False

    recoverable = orch.list_recoverable_runs()
    checks.append({"check": "recoverable-runs", "status": "OK", "detail": str(len(recoverable))})

    stale = orch.stale_artifact_report(max_age_hours=24)
    stale_worktrees = stale.get("stale_worktrees", [])
    stale_branches = stale.get("stale_branches", [])
    stale_ok = len(stale_worktrees) == 0 and len(stale_branches) == 0
    checks.append(
        {
            "check": "stale-artifacts",
            "status": "OK" if stale_ok else "WARN",
            "detail": f"worktrees={len(stale_worktrees)} branches={len(stale_branches)}",
        }
    )

    fix_suggestions: list[dict[str, Any]] = []
    if include_fix_suggestions and failures:
        for plan_path, issues, _summary in failures:
            raw = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                fixes = suggest_fixes(raw, issues)
                fix_suggestions.append(
                    {
                        "plan_path": str(plan_path),
                        "fixes": [fix.model_dump(mode="json") for fix in fixes],
                    }
                )

    return {
        "ok": ok,
        "checks": checks,
        "plan_failures": [
            {"plan_path": str(path), "issues": issues, "summary": summary}
            for path, issues, summary in failures
        ],
        "stale_artifacts": stale,
        "fix_suggestions": fix_suggestions,
    }


def _render_doctor_table(snapshot: dict[str, Any]) -> None:
    table = Table(title="Ralphite Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for row in snapshot.get("checks", []):
        if not isinstance(row, dict):
            continue
        table.add_row(str(row.get("check", "")), str(row.get("status", "")), str(row.get("detail", "")))
    console.print(table)

    for failure in snapshot.get("plan_failures", []):
        if not isinstance(failure, dict):
            continue
        plan_path = str(failure.get("plan_path"))
        issues = failure.get("issues", [])
        summary = failure.get("summary", {})
        console.print(f"\n[bold red]Invalid plan:[/bold red] {plan_path}")
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                console.print(f"  - {issue.get('code')}: {issue.get('message')} ({issue.get('path')})")
        if summary:
            console.print(f"  Summary: {summary}")

    stale = snapshot.get("stale_artifacts", {})
    if not isinstance(stale, dict):
        return
    stale_worktrees = stale.get("stale_worktrees", []) if isinstance(stale.get("stale_worktrees"), list) else []
    stale_branches = stale.get("stale_branches", []) if isinstance(stale.get("stale_branches"), list) else []
    if stale_worktrees or stale_branches:
        console.print("\n[bold yellow]Stale managed artifacts[/bold yellow]")
        for item in stale_worktrees[:10]:
            if not isinstance(item, dict):
                continue
            console.print(f"  - worktree run={item.get('run_id')} age={item.get('age_hours')}h path={item.get('path')}")
        for item in stale_branches[:10]:
            if not isinstance(item, dict):
                continue
            console.print(f"  - branch run={item.get('run_id')} branch={item.get('branch')}")
        console.print("  Action: run cleanup by resolving or resuming stale runs, then re-check doctor.")


def _print_run_stream(orch: LocalOrchestrator, run_id: str, *, verbose: bool = False) -> None:
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
        console.print("\nArtifacts:")
        for artifact in run.artifacts:
            console.print(f"- {artifact['id']}: {artifact['path']}")


def _emit_payload(output: str, payload: dict[str, Any], *, title: str | None = None) -> None:
    if output == "json":
        console.print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if title:
        console.print(f"[bold]{title}[/bold]")
    status = present_run_status(str(payload.get("status", "")))
    console.print(f"Status: {status.label}")
    issues = payload.get("issues", [])
    if isinstance(issues, list) and issues:
        console.print("Issues:")
        for issue in issues:
            if isinstance(issue, dict):
                console.print(f"- {issue.get('code')}: {issue.get('message')}")
            else:
                console.print(f"- {issue}")
    actions = payload.get("next_actions", [])
    if isinstance(actions, list) and actions:
        console.print("Next actions:")
        for item in actions:
            console.print(f"- {item}")


@app.command()
def init(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    profile: Annotated[str | None, typer.Option(help="Profile name for local policy")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Use defaults without prompts")] = False,
) -> None:
    """Initialize a local-first Ralphite workspace."""
    orch = _orchestrator(workspace)

    profile_name = profile or orch.config.profile_name
    if not yes and profile is None:
        profile_name = typer.prompt("Profile name", default=orch.config.profile_name)

    config = LocalConfig(
        workspace_root=str(orch.workspace_root),
        profile_name=profile_name,
        allow_tools=orch.config.allow_tools,
        deny_tools=orch.config.deny_tools,
        allow_mcps=orch.config.allow_mcps,
        deny_mcps=orch.config.deny_mcps,
        compact_timeline=orch.config.compact_timeline,
        default_plan=orch.config.default_plan,
        task_writeback_mode=orch.config.task_writeback_mode,
    )
    cfg_path = save_config(orch.workspace_root, config)
    seeded = seed_starter_if_missing(orch.paths["plans"])

    console.print(f"Initialized workspace: [bold]{orch.workspace_root}[/bold]")
    console.print(f"Config: {cfg_path}")
    if seeded:
        console.print(f"Seeded starter plan: {seeded}")
    else:
        console.print("Starter plan already present.")

@app.command()
def quickstart(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    goal: Annotated[str | None, typer.Option(help="Optional goal to generate a plan")] = None,
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Stream in terminal instead of opening TUI")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Auto-approve capabilities")] = False,
    output: Annotated[str, typer.Option("--output", help="Output mode: table | stream | json")] = "table",
) -> None:
    """Run guided first-run flow: doctor -> plan -> run."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)

    snapshot = _doctor_snapshot(orch, include_fix_suggestions=False)
    if not bool(snapshot.get("ok")):
        if mode == "json":
            _emit_payload(
                mode,
                _result_payload(
                    ok=False,
                    status="failed",
                    run_id=None,
                    exit_code=1,
                    issues=[{"code": "doctor.failed", "message": "workspace checks failed"}],
                    next_actions=["Run `ralphite doctor --output table` to inspect failures."],
                    extra={"doctor": snapshot},
                ),
                title="Quickstart",
            )
        else:
            _render_doctor_table(snapshot)
        raise typer.Exit(code=1)

    plan_ref: str | None = None
    if goal:
        plan_ref = str(orch.goal_to_plan(goal))
        if mode != "json":
            console.print(f"Generated plan from goal: {plan_ref}")
    else:
        plan_ref = str(_resolve_plan_ref(orch, None))
        if mode != "json":
            console.print(f"Using plan: {plan_ref}")

    requirements = orch.collect_requirements(plan_ref=plan_ref)
    if mode != "json":
        console.print(f"Required tools: {requirements['tools'] or ['none']}")
        console.print(f"Required mcps: {requirements['mcps'] or ['none']}")

    if not yes:
        approved = typer.confirm("Approve these capabilities for this run?", default=True)
        if not approved:
            payload = _result_payload(
                ok=False,
                status="cancelled",
                run_id=None,
                exit_code=1,
                issues=[{"code": "quickstart.cancelled", "message": "run aborted by user"}],
            )
            _emit_payload(mode, payload, title="Quickstart")
            raise typer.Exit(code=1)

    run_id = orch.start_run(plan_ref=plan_ref, metadata={"source": "cli.quickstart", "goal": goal})
    if no_tui:
        if mode == "stream":
            _print_run_stream(orch, run_id, verbose=False)
        else:
            orch.wait_for_run(run_id, timeout=60.0)
            run = orch.get_run(run_id)
            status = run.status if run else "unknown"
            payload = _result_payload(
                ok=status == "succeeded",
                status=status,
                run_id=run_id,
                exit_code=0 if status == "succeeded" else 1,
                next_actions=[present_run_status(status).next_action],
                extra={"artifacts": run.artifacts if run else []},
            )
            _emit_payload(mode, payload, title="Quickstart")
        return

    AppShell(orchestrator=orch, run_id=run_id, initial_screen="run_setup").run()


@app.command()
def validate(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Emit machine-readable output")] = False,
    apply_safe_fixes: Annotated[bool, typer.Option("--apply-safe-fixes", help="Write an auto-fixed revision")] = False,
) -> None:
    """Validate a plan and suggest safe fixes."""
    orch = _orchestrator(workspace)
    path = _resolve_plan_ref(orch, plan)
    content = path.read_text(encoding="utf-8")
    valid, issues, summary = validate_plan_content(content, workspace_root=orch.workspace_root)

    raw = yaml.safe_load(content)
    fixes = suggest_fixes(raw if isinstance(raw, dict) else {}, issues)
    payload: dict[str, Any] = {
        "ok": valid,
        "status": "succeeded" if valid else "failed",
        "plan_path": str(path),
        "summary": summary,
        "issues": issues,
        "fixes": [fix.model_dump(mode="json") for fix in fixes],
    }

    if apply_safe_fixes and isinstance(raw, dict) and fixes:
        fixed = dict(raw)
        for fix in fixes:
            fixed = apply_fix(fixed, fix)
        fixed_valid, fixed_issues, _fixed_summary = validate_plan_content(
            yaml.safe_dump(fixed, sort_keys=False, allow_unicode=False),
            workspace_root=orch.workspace_root,
        )
        target = orch.paths["plans"] / f"{path.stem}.fixed.yaml"
        target.write_text(yaml.safe_dump(fixed, sort_keys=False, allow_unicode=False), encoding="utf-8")
        payload["fixed_revision"] = str(target)
        payload["fixed_valid"] = fixed_valid
        payload["fixed_issues"] = fixed_issues

    if json_mode:
        console.print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        console.print(f"Plan: {path}")
        console.print(f"Valid: {'yes' if valid else 'no'}")
        if issues:
            console.print("Issues:")
            for issue in issues:
                console.print(f"- {issue.get('code')}: {issue.get('message')} ({issue.get('path')})")
        if fixes:
            console.print("Suggested safe fixes:")
            for fix in fixes:
                console.print(f"- {fix.title}: {fix.description} ({fix.path})")
        if apply_safe_fixes and payload.get("fixed_revision"):
            console.print(f"Wrote fixed revision: {payload['fixed_revision']}")

    raise typer.Exit(code=0 if valid else 1)


@app.command()
def doctor(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    output: Annotated[str, typer.Option("--output", help="Output mode: table | json")] = "table",
    fix_suggestions: Annotated[bool, typer.Option("--fix-suggestions", help="Include auto-fix suggestions")] = False,
) -> None:
    """Check local environment, plans, and runtime readiness."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    snapshot = _doctor_snapshot(orch, include_fix_suggestions=fix_suggestions)
    if mode == "json":
        console.print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
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
                        console.print(f"  * {fix.get('title')}: {fix.get('description')}")
    if not bool(snapshot.get("ok")):
        raise typer.Exit(code=1)


@app.command()
def run(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    goal: Annotated[str | None, typer.Option(help="Goal text to generate a plan")] = None,
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print streaming logs instead of opening TUI")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Auto-approve requirements")] = False,
    attach_run_detail: Annotated[bool, typer.Option("--attach-run-detail", help="Open phase timeline after start")] = False,
    output: Annotated[str, typer.Option("--output", help="Output mode: stream | table | json")] = "stream",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical run output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra event guidance")] = False,
) -> None:
    """Run a plan immediately with optional TUI monitoring."""
    orch = _orchestrator(workspace)
    output_mode = _normalize_output(output)

    plan_ref = plan
    if goal:
        generated = orch.goal_to_plan(goal)
        plan_ref = str(generated)
        if not quiet and output_mode != "json":
            console.print(f"Generated plan from goal: {generated}")

    requirements = orch.collect_requirements(plan_ref=plan_ref)
    if not quiet and output_mode != "json":
        console.print(f"Required tools: {requirements['tools'] or ['none']}")
        console.print(f"Required mcps: {requirements['mcps'] or ['none']}")

    if not yes:
        approved = typer.confirm("Approve these capabilities for this run?", default=True)
        if not approved:
            if not quiet and output_mode != "json":
                console.print("Run aborted by user.")
            raise typer.Exit(code=1)

    run_id = orch.start_run(plan_ref=plan_ref, metadata={"source": "cli.run", "goal": goal})
    if not quiet and output_mode != "json":
        console.print(f"Started run: [bold]{run_id}[/bold]")

    if no_tui:
        if output_mode == "stream":
            _print_run_stream(orch, run_id, verbose=verbose)
        else:
            orch.wait_for_run(run_id, timeout=60.0)
            run_state = orch.get_run(run_id)
            status = run_state.status if run_state else "unknown"
            payload = _result_payload(
                ok=status == "succeeded",
                status=status,
                run_id=run_id,
                exit_code=0 if status == "succeeded" else 1,
                next_actions=[present_run_status(status).next_action],
                extra={
                    "artifacts": run_state.artifacts if run_state else [],
                    "required_tools": requirements["tools"],
                    "required_mcps": requirements["mcps"],
                },
            )
            _emit_payload(output_mode, payload, title="Run Result")
            if status != "succeeded":
                raise typer.Exit(code=1)
    else:
        initial_screen = "phase_timeline" if attach_run_detail else "runs"
        AppShell(orchestrator=orch, run_id=run_id, initial_screen=initial_screen).run()


@app.command()
def recover(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str | None, typer.Option(help="Run id to recover")] = None,
    mode: Annotated[str, typer.Option(help="Recovery mode: manual | agent_best_effort | abort_phase")] = "manual",
    prompt: Annotated[str | None, typer.Option(help="Prompt used by agent_best_effort mode")] = None,
    preflight_only: Annotated[bool, typer.Option("--preflight-only", help="Validate recovery readiness only")] = False,
    resume: Annotated[bool, typer.Option("--resume/--no-resume", help="Resume immediately after setting mode")] = True,
    json_mode: Annotated[bool, typer.Option("--json", help="Emit machine-readable output")] = False,
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print stream after recover")] = False,
    output: Annotated[str, typer.Option("--output", help="Output mode: stream | table | json")] = "table",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra event guidance")] = False,
) -> None:
    """Recover and resume a checkpointed run with explicit recovery mode and automation semantics."""
    orch = _orchestrator(workspace)
    output_mode = _normalize_output(output, json_mode=json_mode)

    allowed_modes = {"manual", "agent_best_effort", "abort_phase"}
    if mode not in allowed_modes:
        payload = _result_payload(
            ok=False,
            status="failed",
            run_id=run_id,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[{"code": "recover.invalid_mode", "message": f"invalid mode '{mode}'"}],
            next_actions=["Use one of: manual, agent_best_effort, abort_phase."],
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

    target = run_id
    if target is None:
        recoverable = orch.list_recoverable_runs()
        if not recoverable:
            payload = _result_payload(
                ok=False,
                status="failed",
                run_id=None,
                exit_code=RECOVER_EXIT_NO_RECOVERABLE,
                issues=[{"code": "recover.none", "message": "no recoverable runs found"}],
                next_actions=["Run `ralphite history` to inspect previous runs."],
            )
            _emit_payload(output_mode, payload, title="Recovery")
            raise typer.Exit(code=RECOVER_EXIT_NO_RECOVERABLE)
        target = recoverable[-1]

    if not orch.recover_run(target):
        payload = _result_payload(
            ok=False,
            status="failed",
            run_id=target,
            exit_code=RECOVER_EXIT_UNRECOVERABLE,
            issues=[{"code": "recover.unrecoverable", "message": "run not found or unrecoverable"}],
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_UNRECOVERABLE)

    if not orch.set_recovery_mode(target, mode, prompt=prompt):
        payload = _result_payload(
            ok=False,
            status="failed",
            run_id=target,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[{"code": "recover.mode_set_failed", "message": f"unable to set recovery mode '{mode}'"}],
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

    preflight = orch.recovery_preflight(target)
    if preflight_only:
        exit_code = RECOVER_EXIT_SUCCESS if preflight.get("ok") else RECOVER_EXIT_PREFLIGHT_FAILED
        issues = [] if preflight.get("ok") else [{"code": "recover.preflight_failed", "message": "preflight failed"}]
        payload = _result_payload(
            ok=bool(preflight.get("ok")),
            status="succeeded" if preflight.get("ok") else "failed",
            run_id=target,
            exit_code=exit_code,
            issues=issues,
            next_actions=list(preflight.get("blocking_reasons", [])) if isinstance(preflight, dict) else [],
            extra={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery Preflight")
        raise typer.Exit(code=exit_code)

    if not preflight.get("ok"):
        payload = _result_payload(
            ok=False,
            status="failed",
            run_id=target,
            exit_code=RECOVER_EXIT_PREFLIGHT_FAILED,
            issues=[{"code": "recover.preflight_failed", "message": "recovery preflight failed"}],
            next_actions=list(preflight.get("blocking_reasons", [])) if isinstance(preflight, dict) else [],
            extra={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PREFLIGHT_FAILED)

    if not resume:
        payload = _result_payload(
            ok=True,
            status="paused",
            run_id=target,
            exit_code=RECOVER_EXIT_PENDING,
            next_actions=["Run `ralphite recover --resume` to continue."],
            extra={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PENDING)

    resumed = orch.resume_from_checkpoint(target)
    if not resumed:
        latest_preflight = orch.recovery_preflight(target)
        payload = _result_payload(
            ok=False,
            status="paused_recovery_required",
            run_id=target,
            exit_code=RECOVER_EXIT_PENDING,
            issues=[{"code": "recover.resume_rejected", "message": "resume rejected"}],
            next_actions=list(latest_preflight.get("blocking_reasons", []))
            if isinstance(latest_preflight, dict)
            else [],
            extra={"preflight": latest_preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PENDING)

    if no_tui:
        if output_mode == "stream":
            _print_run_stream(orch, target, verbose=verbose)
        else:
            orch.wait_for_run(target, timeout=60.0)
        run = orch.get_run(target)
        status = run.status if run else "unknown"
        if status == "succeeded":
            exit_code = RECOVER_EXIT_SUCCESS
        elif status in {"paused", "paused_recovery_required"}:
            exit_code = RECOVER_EXIT_PENDING
        elif status in {"failed", "cancelled"}:
            exit_code = RECOVER_EXIT_TERMINAL_FAILURE
        else:
            exit_code = RECOVER_EXIT_INTERNAL_ERROR
        payload = _result_payload(
            ok=exit_code == RECOVER_EXIT_SUCCESS,
            status=status,
            run_id=target,
            exit_code=exit_code,
            next_actions=[present_run_status(status).next_action],
            extra={"artifacts": run.artifacts if run else []},
        )
        _emit_payload(output_mode, payload, title="Recovery Result")
        raise typer.Exit(code=exit_code)

    if not quiet and output_mode != "json":
        console.print(f"Recovery mode set and resumed for run: [bold]{target}[/bold]")
    AppShell(orchestrator=orch, run_id=target, initial_screen="recovery").run()


@app.command()
def history(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    query: Annotated[str | None, typer.Option(help="Search by id/status/path")] = None,
    limit: Annotated[int, typer.Option(help="Max rows")] = 20,
    output: Annotated[str, typer.Option("--output", help="Output mode: table | json")] = "table",
) -> None:
    """Show local run history."""
    orch = _orchestrator(workspace)
    rows = orch.list_history(limit=limit, query=query)
    mode = _normalize_output(output)

    if mode == "json":
        payload = [
            {
                "run_id": run.id,
                "status": run.status,
                "status_label": present_run_status(run.status).label,
                "next_action": present_run_status(run.status).next_action,
                "plan": run.plan_path,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
            }
            for run in rows
        ]
        console.print(json.dumps(payload, indent=2, sort_keys=True))
        return

    table = Table(title="Run History")
    table.add_column("Run ID")
    table.add_column("Status")
    table.add_column("Next Action")
    table.add_column("Plan")
    table.add_column("Created")
    table.add_column("Completed")
    for run in rows:
        status = present_run_status(run.status)
        table.add_row(run.id, status.label, status.next_action, run.plan_path, run.created_at, run.completed_at or "-")
    console.print(table)


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="Existing run id")],
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print streaming logs instead of opening TUI")] = False,
) -> None:
    """Replay a previous run in rerun-failed mode."""
    orch = _orchestrator(workspace)
    new_run_id = orch.rerun_failed(run_id)
    console.print(f"Replay started: {new_run_id} (from {run_id})")
    if no_tui:
        _print_run_stream(orch, new_run_id)
    else:
        AppShell(orchestrator=orch, run_id=new_run_id, initial_screen="phase_timeline").run()


def _run_release_gate(orch: LocalOrchestrator) -> bool:
    suites = [
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "packages/engine/tests/test_task_parser.py",
            "packages/engine/tests/test_structure_compiler.py",
            "-q",
        ],
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "packages/engine/tests/test_git_worktree_integration.py",
            "packages/engine/tests/test_orchestrator.py",
            "packages/engine/tests/test_recovery.py",
            "-q",
        ],
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "apps/tui/tests",
            "-q",
        ],
        [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "packages/engine/tests/test_e2e_recovery.py",
            "-q",
        ],
    ]
    for command in suites:
        console.print(f"Running release gate suite: {' '.join(command)}")
        result = subprocess.run(command, cwd=orch.workspace_root, check=False)
        if result.returncode != 0:
            return False
    return True


@app.command()
def check(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    full: Annotated[bool, typer.Option("--full", help="Run full repo test suite")] = False,
    release_gate: Annotated[bool, typer.Option("--release-gate", help="Run v4 stabilization release gate suites")] = False,
) -> None:
    """Run baseline quality gates for local UX reliability."""
    orch = _orchestrator(workspace)
    snapshot = _doctor_snapshot(orch, include_fix_suggestions=False)
    _render_doctor_table(snapshot)
    if not bool(snapshot.get("ok")):
        raise typer.Exit(code=1)

    compile_targets = []
    for relative in ("packages/engine/src", "apps/tui/src"):
        target = orch.workspace_root / relative
        if target.exists():
            compile_targets.append(relative)
    if compile_targets:
        compile_cmd = [sys.executable, "-m", "compileall", *compile_targets]
        compile_result = subprocess.run(compile_cmd, cwd=orch.workspace_root, check=False)
        if compile_result.returncode != 0:
            raise typer.Exit(code=1)

    if full:
        command = ["uv", "run", "--with", "pytest", "pytest", "packages/engine/tests", "apps/tui/tests", "-q"]
        console.print(f"Running: {' '.join(command)}")
        result = subprocess.run(command, cwd=orch.workspace_root, check=False)
        if result.returncode != 0:
            raise typer.Exit(code=1)

    if release_gate:
        if not _run_release_gate(orch):
            raise typer.Exit(code=1)

    console.print("[green]ralphite check passed[/green]")


@app.command()
def tui(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    screen: Annotated[str, typer.Option(help="Initial screen")] = "home",
    run_id: Annotated[str | None, typer.Option(help="Current run id")] = None,
) -> None:
    """Open the Ralphite terminal shell."""
    orch = _orchestrator(workspace)
    AppShell(orchestrator=orch, run_id=run_id, initial_screen=screen).run()


if __name__ == "__main__":
    app()
