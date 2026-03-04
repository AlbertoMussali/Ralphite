from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Annotated, Any

from rich.console import Console
from rich.table import Table
import typer
import yaml
from ralphite_schemas import CliOutputEnvelopeV1

from ralphite_engine import (
    LocalConfig,
    LocalOrchestrator,
    apply_fix,
    migrate_v4_to_v5,
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
CLI_OUTPUT_SCHEMA_VERSION = "cli-output.v1"


def _orchestrator(workspace: Path, *, bootstrap: bool = True) -> LocalOrchestrator:
    return LocalOrchestrator(workspace.expanduser().resolve(), bootstrap=bootstrap)


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
        valid, issues, summary = validate_plan_content(
            content,
            workspace_root=orch.workspace_root,
            plan_path=str(plan_path),
        )
        if valid:
            continue
        failures.append((plan_path, issues, summary))
    return len(failures) == 0, failures


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
    resolver_ok = True
    git_ready_ok = True
    for plan in plans:
        valid, _issues, summary = validate_plan_content(
            plan.read_text(encoding="utf-8"),
            workspace_root=orch.workspace_root,
            plan_path=str(plan),
        )
        if not valid:
            continue
        task_status = str(summary.get("tasks_status", {}).get("status", "unknown"))
        if task_status not in {"ok", "issues"}:
            tasks_ok = False
        resolved = summary.get("resolved_execution", {})
        if not isinstance(resolved, dict):
            resolver_ok = False
        elif not isinstance(resolved.get("resolved_nodes"), list):
            resolver_ok = False
        readiness = summary.get("recovery_readiness", {})
        if str(readiness.get("status")) not in {"ready", "dirty", "degraded"}:
            git_ready_ok = False

    checks.append({"check": "tasks", "status": "OK" if tasks_ok else "FAIL", "detail": "embedded YAML tasks parseable"})
    if not tasks_ok:
        ok = False

    checks.append(
        {
            "check": "orchestration-resolver",
            "status": "OK" if resolver_ok else "FAIL",
            "detail": "resolved execution structure is available",
        }
    )
    if not resolver_ok:
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


def _doctor_evaluation(snapshot: dict[str, Any], *, strict: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    non_critical = {"stale-artifacts", "recovery-readiness"}
    for row in snapshot.get("checks", []):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", "")).upper()
        check_name = str(row.get("check", ""))
        if status in {"OK", "PASS"}:
            continue
        if status == "WARN":
            warnings.append(row)
            if strict:
                blocking.append(row)
            continue
        if not strict and check_name in non_critical:
            warnings.append(row)
            continue
        blocking.append(row)
    return blocking, warnings


def _collect_recommended_commands(snapshot: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for failure in snapshot.get("plan_failures", []):
        if not isinstance(failure, dict):
            continue
        summary = failure.get("summary")
        if not isinstance(summary, dict):
            continue
        rec = summary.get("recommended_commands")
        if not isinstance(rec, list):
            continue
        for item in rec:
            if isinstance(item, str) and item.strip():
                commands.append(item.strip())
    # Keep insertion order while removing duplicates.
    return list(dict.fromkeys(commands))


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
        has_unsupported = False
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                if str(issue.get("code")) == "version.unsupported":
                    has_unsupported = True
                console.print(f"  - {issue.get('code')}: {issue.get('message')} ({issue.get('path')})")
        if summary:
            console.print(f"  Summary: {summary}")
        recommended = summary.get("recommended_commands", []) if isinstance(summary, dict) else []
        if isinstance(recommended, list) and recommended:
            console.print("  Recommended commands:")
            for cmd in recommended:
                if isinstance(cmd, str):
                    console.print(f"  - {cmd}")
        elif has_unsupported:
            console.print(f"  - uv run ralphite migrate --plan {plan_path}")

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
        # JSON mode must remain machine-parseable with no Rich wrapping or formatting.
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        sys.stdout.flush()
        return
    if title:
        console.print(f"[bold]{title}[/bold]")
    status = present_run_status(str(payload.get("status", "")))
    console.print(f"Status: {status.label}")
    run_id = payload.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        console.print(f"Run ID: {run_id}")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if isinstance(data, dict):
        plan_path = data.get("plan_path")
        if isinstance(plan_path, str) and plan_path.strip():
            console.print(f"Plan: {plan_path}")
        artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), list) else []
        if artifacts:
            console.print(f"Artifacts: {len(artifacts)}")
            shown = 0
            for item in artifacts:
                if not isinstance(item, dict):
                    continue
                path = item.get("path")
                if not isinstance(path, str) or not path:
                    continue
                shown += 1
                console.print(f"- {path}")
                if shown >= 3:
                    break
            if len(artifacts) > shown:
                console.print(f"- ... ({len(artifacts) - shown} more)")
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
    strict_doctor: Annotated[bool, typer.Option("--strict-doctor", help="Fail on any doctor warning")] = False,
    bootstrap: Annotated[
        bool,
        typer.Option("--bootstrap/--no-bootstrap", help="Auto-init missing config and starter plan"),
    ] = True,
    output: Annotated[str, typer.Option("--output", help="Output mode: table | stream | json")] = "table",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra event guidance")] = False,
) -> None:
    """Run guided first-run flow: doctor -> plan -> run."""
    mode = _normalize_output(output)
    if mode == "json" and not no_tui:
        payload = _result_payload(
            command="quickstart",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[{"code": "quickstart.no_tui_required", "message": "use --no-tui for JSON output"}],
            next_actions=["Re-run with --no-tui --output json."],
        )
        _emit_payload(mode, payload, title="Quickstart")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

    workspace_root = workspace.expanduser().resolve()
    config_before = (workspace_root / ".ralphite" / "config.toml").exists()
    plans_dir = workspace_root / ".ralphite" / "plans"
    plans_before = plans_dir.exists() and any(path.suffix.lower() in {".yaml", ".yml"} for path in plans_dir.glob("*"))
    orch = _orchestrator(workspace, bootstrap=bootstrap)

    step_index = 0
    steps: list[dict[str, Any]] = []

    def record_step(name: str, started: float, detail: str = "") -> None:
        nonlocal step_index
        step_index += 1
        elapsed = round(max(0.0, time.perf_counter() - started), 3)
        row = {"order": step_index, "step": name, "elapsed_seconds": elapsed, "detail": detail}
        steps.append(row)
        if mode != "json" and not quiet:
            suffix = f" - {detail}" if detail else ""
            console.print(f"{step_index}. {name}{suffix} ({elapsed:.2f}s)")

    if mode != "json" and not quiet:
        console.print("Quickstart Flow")

    doctor_started = time.perf_counter()
    snapshot = _doctor_snapshot(orch, include_fix_suggestions=False)
    blocking_checks, warning_checks = _doctor_evaluation(snapshot, strict=strict_doctor)
    doctor_detail = f"blocking={len(blocking_checks)} warnings={len(warning_checks)} strict={strict_doctor}"
    record_step("Doctor", doctor_started, doctor_detail)
    if blocking_checks:
        recommended = _collect_recommended_commands(snapshot)
        next_actions = recommended or ["Run `ralphite doctor --output table` to inspect failures."]
        if mode == "json":
            _emit_payload(
                mode,
                _result_payload(
                    command="quickstart",
                    ok=False,
                    status="failed",
                    run_id=None,
                    exit_code=1,
                    issues=[{"code": "doctor.failed", "message": "workspace checks failed"}],
                    next_actions=next_actions,
                    data={
                        "doctor": snapshot,
                        "strict_doctor": strict_doctor,
                        "warnings": warning_checks,
                        "step_timing": steps,
                    },
                ),
                title="Quickstart",
            )
        else:
            _render_doctor_table(snapshot)
            if recommended:
                console.print("Recommended commands:")
                for cmd in recommended:
                    console.print(f"- {cmd}")
        raise typer.Exit(code=1)

    if bootstrap and mode != "json" and not quiet:
        config_after = orch.paths["config"].exists()
        plans_after = bool(orch.list_plans())
        created: list[str] = []
        if not config_before and config_after:
            created.append(str(orch.paths["config"]))
        if not plans_before and plans_after:
            created.append(str(orch.paths["plans"]))
        if created:
            console.print(f"Bootstrap: initialized {', '.join(created)}")

    plan_started = time.perf_counter()
    plan_ref: str | None = None
    if goal:
        plan_ref = str(orch.goal_to_plan(goal))
    else:
        plan_ref = str(_resolve_plan_ref(orch, None))
    record_step("Plan Selection", plan_started, Path(plan_ref).name if isinstance(plan_ref, str) else "none")

    approval_started = time.perf_counter()
    requirements = orch.collect_requirements(plan_ref=plan_ref)
    approved = True
    if not quiet and mode != "json":
        console.print(f"Required tools: {requirements['tools'] or ['none']}")
        console.print(f"Required mcps: {requirements['mcps'] or ['none']}")

    if not yes:
        approved = typer.confirm("Approve these capabilities for this run?", default=True)
        if not approved:
            record_step("Capability Approval", approval_started, "cancelled")
            payload = _result_payload(
                command="quickstart",
                ok=False,
                status="cancelled",
                run_id=None,
                exit_code=1,
                issues=[{"code": "quickstart.cancelled", "message": "run aborted by user"}],
                data={"step_timing": steps},
            )
            _emit_payload(mode, payload, title="Quickstart")
            raise typer.Exit(code=1)
    record_step("Capability Approval", approval_started, "approved" if approved else "cancelled")

    run_started = time.perf_counter()
    run_id = orch.start_run(plan_ref=plan_ref, metadata={"source": "cli.quickstart", "goal": goal})
    run_status = "running"
    if no_tui:
        if mode == "stream":
            _print_run_stream(orch, run_id, verbose=verbose)
            run_state = orch.get_run(run_id)
            run_status = run_state.status if run_state else "unknown"
        else:
            orch.wait_for_run(run_id, timeout=60.0)
            run = orch.get_run(run_id)
            status = run.status if run else "unknown"
            run_status = status
            record_step("Run", run_started, status)
            payload = _result_payload(
                command="quickstart",
                ok=status == "succeeded",
                status=status,
                run_id=run_id,
                exit_code=0 if status == "succeeded" else 1,
                next_actions=[present_run_status(status).next_action],
                data={
                    "artifacts": run.artifacts if run else [],
                    "plan_path": str(plan_ref) if plan_ref else "",
                    "required_tools": requirements["tools"],
                    "required_mcps": requirements["mcps"],
                    "step_timing": steps,
                    "doctor_warnings": warning_checks,
                },
            )
            _emit_payload(mode, payload, title="Quickstart")
            if status != "succeeded":
                raise typer.Exit(code=1)
        if mode == "stream":
            record_step("Run", run_started, run_status)
        return

    record_step("Run", run_started, f"started {run_id}")
    AppShell(orchestrator=orch, run_id=run_id, initial_screen="run_setup").run()


@app.command()
def validate(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Emit machine-readable output")] = False,
    apply_safe_fixes: Annotated[bool, typer.Option("--apply-safe-fixes", help="Write an auto-fixed revision")] = False,
    output: Annotated[str, typer.Option("--output", help="Output mode: table | json")] = "table",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra details")] = False,
) -> None:
    """Validate a plan and suggest safe fixes."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output, json_mode=json_mode)
    path = _resolve_plan_ref(orch, plan)
    content = path.read_text(encoding="utf-8")
    valid, issues, summary = validate_plan_content(
        content,
        workspace_root=orch.workspace_root,
        plan_path=str(path),
    )

    raw = yaml.safe_load(content)
    fixes = suggest_fixes(raw if isinstance(raw, dict) else {}, issues)
    recommended_commands = summary.get("recommended_commands", []) if isinstance(summary, dict) else []
    if not isinstance(recommended_commands, list):
        recommended_commands = []
    if any(str(issue.get("code")) == "version.unsupported" for issue in issues):
        recommended_commands = [
            f"uv run ralphite migrate --workspace {orch.workspace_root} --plan {path}",
            *[item for item in recommended_commands if isinstance(item, str)],
        ]
    recommended_commands = [item for item in recommended_commands if isinstance(item, str) and item.strip()]
    recommended_commands = list(dict.fromkeys(recommended_commands))
    payload: dict[str, Any] = {
        "ok": valid,
        "status": "succeeded" if valid else "failed",
        "plan_path": str(path),
        "summary": summary,
        "issues": issues,
        "fixes": [fix.model_dump(mode="json") for fix in fixes],
        "recommended_commands": recommended_commands,
    }

    if apply_safe_fixes and isinstance(raw, dict) and fixes:
        fixed = dict(raw)
        for fix in fixes:
            fixed = apply_fix(fixed, fix)
        fixed_valid, fixed_issues, _fixed_summary = validate_plan_content(
            yaml.safe_dump(fixed, sort_keys=False, allow_unicode=False),
            workspace_root=orch.workspace_root,
            plan_path=str(path),
        )
        target = orch.paths["plans"] / f"{path.stem}.fixed.yaml"
        target.write_text(yaml.safe_dump(fixed, sort_keys=False, allow_unicode=False), encoding="utf-8")
        payload["fixed_revision"] = str(target)
        payload["fixed_valid"] = fixed_valid
        payload["fixed_issues"] = fixed_issues

    envelope = _result_payload(
        command="validate",
        ok=bool(payload.get("ok")),
        status=str(payload.get("status", "unknown")),
        run_id=None,
        exit_code=0 if valid else 1,
        issues=issues,
        next_actions=(
            recommended_commands
            if recommended_commands
            else (["Review suggested safe fixes and rerun validate."] if not valid else ["Validation passed."])
        ),
        data=payload,
    )
    if mode == "json":
        _emit_payload(mode, envelope)
    else:
        if not quiet:
            console.print(f"Plan: {path}")
            console.print(f"Valid: {'yes' if valid else 'no'}")
        if issues:
            console.print("Issues:")
            for issue in issues:
                console.print(f"- {issue.get('code')}: {issue.get('message')} ({issue.get('path')})")
        if fixes and (verbose or not quiet):
            console.print("Suggested safe fixes:")
            for fix in fixes:
                console.print(f"- {fix.title}: {fix.description} ({fix.path})")
        if recommended_commands and (verbose or not quiet):
            console.print("Recommended commands:")
            for cmd in recommended_commands:
                console.print(f"- {cmd}")
        if apply_safe_fixes and payload.get("fixed_revision"):
            console.print(f"Wrote fixed revision: {payload['fixed_revision']}")

    raise typer.Exit(code=0 if valid else 1)


@app.command()
def migrate(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    output: Annotated[str, typer.Option("--output", help="Output mode: table | json")] = "table",
    json_mode: Annotated[bool, typer.Option("--json", help="Emit machine-readable output")] = False,
    strict: Annotated[bool, typer.Option("--strict", help="Fail when converted plan still has validation issues")] = False,
    in_place: Annotated[bool, typer.Option("--in-place", help="Overwrite original plan path")] = False,
) -> None:
    """Migrate a v4 plan to v5 orchestration format."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output, json_mode=json_mode)
    path = _resolve_plan_ref(orch, plan)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        payload = _result_payload(
            command="migrate",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=1,
            issues=[{"code": "yaml.invalid", "message": "plan content must be a YAML object"}],
        )
        _emit_payload(mode, payload, title="Migrate")
        raise typer.Exit(code=1)

    version = int(raw.get("version", 1) or 1)
    if version == 5:
        envelope = _result_payload(
            command="migrate",
            ok=True,
            status="succeeded",
            run_id=None,
            exit_code=0,
            next_actions=["Plan is already v5; no migration required."],
            data={"source_plan": str(path), "version": 5, "migrated": False, "target_plan": str(path)},
        )
        _emit_payload(mode, envelope, title="Migrate")
        return
    if version != 4:
        envelope = _result_payload(
            command="migrate",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=1,
            issues=[{"code": "version.unsupported", "message": "migrate only supports version 4 -> 5"}],
            data={"source_plan": str(path), "version": version},
        )
        _emit_payload(mode, envelope, title="Migrate")
        raise typer.Exit(code=1)

    migrated = migrate_v4_to_v5(raw)
    content = yaml.safe_dump(migrated, sort_keys=False, allow_unicode=False)
    valid, issues, summary = validate_plan_content(
        content,
        workspace_root=orch.workspace_root,
        plan_path=str(path),
    )
    if strict and not valid:
        envelope = _result_payload(
            command="migrate",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=1,
            issues=issues,
            data={"source_plan": str(path), "version": version, "summary": summary},
        )
        _emit_payload(mode, envelope, title="Migrate")
        raise typer.Exit(code=1)

    if in_place:
        target = path
    else:
        target = path.with_name(f"{path.stem}.v5.yaml")
    target.write_text(content, encoding="utf-8")

    envelope = _result_payload(
        command="migrate",
        ok=valid,
        status="succeeded" if valid else "failed",
        run_id=None,
        exit_code=0 if valid else 1,
        issues=issues,
        next_actions=["Run `ralphite validate --plan <target>` to review diagnostics."],
        data={
            "source_plan": str(path),
            "target_plan": str(target),
            "version": 5,
            "migrated": True,
            "summary": summary,
        },
    )
    _emit_payload(mode, envelope, title="Migrate")
    raise typer.Exit(code=0 if valid else 1)


@app.command()
def doctor(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    output: Annotated[str, typer.Option("--output", help="Output mode: table | json")] = "table",
    fix_suggestions: Annotated[bool, typer.Option("--fix-suggestions", help="Include auto-fix suggestions")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra details")] = False,
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
        issues=[{"code": "doctor.failed", "message": "one or more checks failed"}] if not bool(snapshot.get("ok")) else [],
        next_actions=["Run with --fix-suggestions to view suggested plan repairs."] if fix_suggestions else [],
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
                        console.print(f"  * {fix.get('title')}: {fix.get('description')}")
        elif verbose and not quiet:
            console.print("No fix suggestions available.")
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
    if output_mode == "json" and not no_tui:
        payload = _result_payload(
            command="run",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[{"code": "run.no_tui_required", "message": "use --no-tui for JSON output"}],
            next_actions=["Re-run with --no-tui --output json."],
        )
        _emit_payload(output_mode, payload, title="Run")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

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
                command="run",
                ok=status == "succeeded",
                status=status,
                run_id=run_id,
                exit_code=0 if status == "succeeded" else 1,
                next_actions=[present_run_status(status).next_action],
                data={
                    "artifacts": run_state.artifacts if run_state else [],
                    "plan_path": str(plan_ref or ""),
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
    if output_mode == "json" and not no_tui and not preflight_only:
        payload = _result_payload(
            command="recover",
            ok=False,
            status="failed",
            run_id=run_id,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[{"code": "recover.no_tui_required", "message": "use --no-tui for JSON output"}],
            next_actions=["Re-run with --no-tui --output json, or use --preflight-only."],
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

    allowed_modes = {"manual", "agent_best_effort", "abort_phase"}
    if mode not in allowed_modes:
        payload = _result_payload(
            command="recover",
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
                command="recover",
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
            command="recover",
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
            command="recover",
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
            command="recover",
            ok=bool(preflight.get("ok")),
            status="succeeded" if preflight.get("ok") else "failed",
            run_id=target,
            exit_code=exit_code,
            issues=issues,
            next_actions=list(preflight.get("blocking_reasons", [])) if isinstance(preflight, dict) else [],
            data={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery Preflight")
        raise typer.Exit(code=exit_code)

    if not preflight.get("ok"):
        payload = _result_payload(
            command="recover",
            ok=False,
            status="failed",
            run_id=target,
            exit_code=RECOVER_EXIT_PREFLIGHT_FAILED,
            issues=[{"code": "recover.preflight_failed", "message": "recovery preflight failed"}],
            next_actions=list(preflight.get("blocking_reasons", [])) if isinstance(preflight, dict) else [],
            data={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PREFLIGHT_FAILED)

    if not resume:
        payload = _result_payload(
            command="recover",
            ok=True,
            status="paused",
            run_id=target,
            exit_code=RECOVER_EXIT_PENDING,
            next_actions=["Run `ralphite recover --resume` to continue."],
            data={"preflight": preflight},
        )
        _emit_payload(output_mode, payload, title="Recovery")
        raise typer.Exit(code=RECOVER_EXIT_PENDING)

    resumed = orch.resume_from_checkpoint(target)
    if not resumed:
        latest_preflight = orch.recovery_preflight(target)
        payload = _result_payload(
            command="recover",
            ok=False,
            status="paused_recovery_required",
            run_id=target,
            exit_code=RECOVER_EXIT_PENDING,
            issues=[{"code": "recover.resume_rejected", "message": "resume rejected"}],
            next_actions=list(latest_preflight.get("blocking_reasons", []))
            if isinstance(latest_preflight, dict)
            else [],
            data={"preflight": latest_preflight},
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
            command="recover",
            ok=exit_code == RECOVER_EXIT_SUCCESS,
            status=status,
            run_id=target,
            exit_code=exit_code,
            next_actions=[present_run_status(status).next_action],
            data={"artifacts": run.artifacts if run else []},
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
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra details")] = False,
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
    for run in rows:
        status = present_run_status(run.status)
        table.add_row(run.id, status.label, status.next_action, run.plan_path, run.created_at, run.completed_at or "-")
    console.print(table)


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="Existing run id")],
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print streaming logs instead of opening TUI")] = False,
    output: Annotated[str, typer.Option("--output", help="Output mode: stream | table | json")] = "stream",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra event guidance")] = False,
) -> None:
    """Replay a previous run in rerun-failed mode."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    if mode == "json" and not no_tui:
        payload = _result_payload(
            command="replay",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=RECOVER_EXIT_INVALID_INPUT,
            issues=[{"code": "replay.no_tui_required", "message": "use --no-tui for JSON output"}],
            next_actions=["Re-run with --no-tui --output json."],
        )
        _emit_payload(mode, payload, title="Replay")
        raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)

    new_run_id = orch.rerun_failed(run_id)
    if not quiet and mode != "json":
        console.print(f"Replay started: {new_run_id} (from {run_id})")
    if no_tui:
        if mode == "stream":
            _print_run_stream(orch, new_run_id, verbose=verbose)
            return
        orch.wait_for_run(new_run_id, timeout=60.0)
        run = orch.get_run(new_run_id)
        status = run.status if run else "unknown"
        payload = _result_payload(
            command="replay",
            ok=status == "succeeded",
            status=status,
            run_id=new_run_id,
            exit_code=0 if status == "succeeded" else 1,
            next_actions=[present_run_status(status).next_action],
            data={"source_run_id": run_id, "artifacts": run.artifacts if run else []},
        )
        _emit_payload(mode, payload, title="Replay")
        if status != "succeeded":
            raise typer.Exit(code=1)
    else:
        AppShell(orchestrator=orch, run_id=new_run_id, initial_screen="phase_timeline").run()


def _run_release_gate(
    orch: LocalOrchestrator,
    *,
    quiet: bool = False,
    machine_mode: bool = False,
    verbose: bool = False,
) -> tuple[bool, list[dict[str, Any]]]:
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
    results: list[dict[str, Any]] = []
    capture_subprocess_output = machine_mode or quiet
    for command in suites:
        if not quiet and not machine_mode:
            console.print(f"Running release gate suite: {' '.join(command)}")
        result = subprocess.run(
            command,
            cwd=orch.workspace_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        results.append(
            {
                "command": " ".join(command),
                "exit_code": result.returncode,
                "stdout": result.stdout if capture_subprocess_output and verbose else "",
                "stderr": result.stderr if capture_subprocess_output and verbose else "",
            }
        )
        if result.returncode != 0:
            return False, results
    return True, results


@app.command()
def check(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    full: Annotated[bool, typer.Option("--full", help="Run full repo test suite")] = False,
    release_gate: Annotated[bool, typer.Option("--release-gate", help="Run v5 stabilization release gate suites")] = False,
    output: Annotated[str, typer.Option("--output", help="Output mode: table | json")] = "table",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra details")] = False,
) -> None:
    """Run baseline quality gates for local UX reliability."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    machine_mode = mode == "json"
    capture_subprocess_output = machine_mode or quiet

    snapshot = _doctor_snapshot(orch, include_fix_suggestions=False)
    if not machine_mode and not quiet:
        _render_doctor_table(snapshot)
    command_results: list[dict[str, Any]] = []
    if not bool(snapshot.get("ok")):
        envelope = _result_payload(
            command="check",
            ok=False,
            status="failed",
            run_id=None,
            exit_code=1,
            issues=[{"code": "check.doctor_failed", "message": "doctor checks failed"}],
            data={"doctor": snapshot, "commands": command_results},
        )
        _emit_payload(mode, envelope, title="Check")
        raise typer.Exit(code=1)

    compile_targets = []
    for relative in ("packages/engine/src", "apps/tui/src"):
        target = orch.workspace_root / relative
        if target.exists():
            compile_targets.append(relative)
    if compile_targets:
        compile_cmd = [sys.executable, "-m", "compileall", *compile_targets]
        compile_result = subprocess.run(
            compile_cmd,
            cwd=orch.workspace_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        command_results.append(
            {
                "command": " ".join(compile_cmd),
                "exit_code": compile_result.returncode,
                "stdout": compile_result.stdout if capture_subprocess_output and verbose else "",
                "stderr": compile_result.stderr if capture_subprocess_output and verbose else "",
            }
        )
        if compile_result.returncode != 0:
            envelope = _result_payload(
                command="check",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[{"code": "check.compile_failed", "message": "compileall failed"}],
                data={"doctor": snapshot, "commands": command_results},
            )
            _emit_payload(mode, envelope, title="Check")
            raise typer.Exit(code=1)

    if full:
        command = ["uv", "run", "--with", "pytest", "pytest", "packages/engine/tests", "apps/tui/tests", "-q"]
        if not quiet and not machine_mode:
            console.print(f"Running: {' '.join(command)}")
        result = subprocess.run(
            command,
            cwd=orch.workspace_root,
            check=False,
            capture_output=capture_subprocess_output,
            text=True,
        )
        command_results.append(
            {
                "command": " ".join(command),
                "exit_code": result.returncode,
                "stdout": result.stdout if capture_subprocess_output and verbose else "",
                "stderr": result.stderr if capture_subprocess_output and verbose else "",
            }
        )
        if result.returncode != 0:
            envelope = _result_payload(
                command="check",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[{"code": "check.pytest_failed", "message": "full test suite failed"}],
                data={"doctor": snapshot, "commands": command_results},
            )
            _emit_payload(mode, envelope, title="Check")
            raise typer.Exit(code=1)

    if release_gate:
        gate_ok, gate_results = _run_release_gate(orch, quiet=quiet, machine_mode=machine_mode, verbose=verbose)
        command_results.extend(gate_results)
        if not gate_ok:
            envelope = _result_payload(
                command="check",
                ok=False,
                status="failed",
                run_id=None,
                exit_code=1,
                issues=[{"code": "check.release_gate_failed", "message": "release gate suite failed"}],
                data={"doctor": snapshot, "commands": command_results},
            )
            _emit_payload(mode, envelope, title="Check")
            raise typer.Exit(code=1)

    envelope = _result_payload(
        command="check",
        ok=True,
        status="succeeded",
        run_id=None,
        exit_code=0,
        data={"doctor": snapshot, "commands": command_results},
    )
    if machine_mode:
        _emit_payload(mode, envelope)
    elif not quiet:
        console.print("[green]ralphite check passed[/green]")


@app.command()
def tui(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    screen: Annotated[str, typer.Option(help="Initial screen")] = "home",
    run_id: Annotated[str | None, typer.Option(help="Current run id")] = None,
    output: Annotated[str, typer.Option("--output", help="Output mode: table | json")] = "table",
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress non-critical output")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show extra details")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate launch parameters without opening TUI")] = False,
) -> None:
    """Open the Ralphite terminal shell."""
    orch = _orchestrator(workspace)
    mode = _normalize_output(output)
    if mode == "json":
        if not dry_run:
            payload = _result_payload(
                command="tui",
                ok=False,
                status="failed",
                run_id=run_id,
                exit_code=RECOVER_EXIT_INVALID_INPUT,
                issues=[{"code": "tui.dry_run_required", "message": "--output json requires --dry-run"}],
                next_actions=["Re-run with --dry-run for machine-readable launch metadata."],
                data={"workspace": str(orch.workspace_root), "screen": screen},
            )
            _emit_payload(mode, payload, title="TUI")
            raise typer.Exit(code=RECOVER_EXIT_INVALID_INPUT)
        payload = _result_payload(
            command="tui",
            ok=True,
            status="succeeded",
            run_id=run_id,
            exit_code=0,
            data={"workspace": str(orch.workspace_root), "screen": screen, "dry_run": True},
        )
        _emit_payload(mode, payload, title="TUI")
        return
    if dry_run:
        if not quiet:
            console.print(f"TUI launch dry-run OK: workspace={orch.workspace_root} screen={screen} run_id={run_id or '-'}")
        if verbose and not quiet:
            console.print("Use `ralphite tui` without --dry-run to open the interactive shell.")
        return
    AppShell(orchestrator=orch, run_id=run_id, initial_screen=screen).run()


if __name__ == "__main__":
    app()
