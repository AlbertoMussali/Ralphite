from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
from typing import Annotated

from rich.console import Console
from rich.table import Table
import typer
import yaml

from ralphite_engine import (
    LocalConfig,
    LocalOrchestrator,
    migrate_plan_file,
    migrate_plan_in_place,
    save_config,
    seed_starter_if_missing,
    validate_plan_content,
)
from ralphite_tui.tui.app_shell import AppShell

app = typer.Typer(help="Ralphite terminal-first orchestrator", no_args_is_help=True, add_completion=False)
console = Console()


def _orchestrator(workspace: Path) -> LocalOrchestrator:
    return LocalOrchestrator(workspace.expanduser().resolve())


def _validate_all_plans(orch: LocalOrchestrator) -> tuple[bool, list[tuple[Path, list[dict], dict]]]:
    failures: list[tuple[Path, list[dict], dict]] = []
    for plan_path in orch.list_plans():
        content = plan_path.read_text(encoding="utf-8")
        valid, issues, summary = validate_plan_content(content, workspace_root=orch.workspace_root)
        if valid:
            continue
        failures.append((plan_path, issues, summary))
    return len(failures) == 0, failures


def _strict_migration_preflight(orch: LocalOrchestrator) -> tuple[bool, list[str]]:
    messages: list[str] = []
    blocked = False
    for plan_path in orch.list_plans():
        result = migrate_plan_in_place(plan_path)
        messages.append(f"checked {plan_path.name}")
        for warning in result.warnings:
            messages.append(f"  - {warning}")
        if not result.valid:
            blocked = True
            for issue in result.issues:
                messages.append(f"  - BLOCK {issue.get('code')}: {issue.get('message')} ({issue.get('path')})")
    return (not blocked), messages


def _doctor_report(orch: LocalOrchestrator) -> bool:
    table = Table(title="Ralphite Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    ok = True

    required_cmds = ["python3", "uv", "git", "rg"]
    for cmd in required_cmds:
        found = shutil.which(cmd)
        status = "OK" if found else "MISSING"
        if not found:
            ok = False
        table.add_row(f"cmd:{cmd}", status, found or "not in PATH")

    cfg_path = orch.paths["config"]
    cfg_ok = cfg_path.exists()
    table.add_row("config", "OK" if cfg_ok else "MISSING", str(cfg_path))
    if not cfg_ok:
        ok = False

    plans = orch.list_plans()
    table.add_row("plans", "OK" if plans else "MISSING", f"{len(plans)} plan file(s)")
    if not plans:
        ok = False

    valid_plans, failures = _validate_all_plans(orch)
    table.add_row("plan-validation", "OK" if valid_plans else "FAIL", "all plans valid" if valid_plans else f"{len(failures)} invalid")
    if not valid_plans:
        ok = False

    task_source_ok = True
    git_ready_ok = True
    for plan in plans:
        valid, issues, summary = validate_plan_content(plan.read_text(encoding="utf-8"), workspace_root=orch.workspace_root)
        if not valid:
            continue
        task_status = str(summary.get("task_source_status", {}).get("status", "unknown"))
        if task_status not in {"ok", "issues"}:
            task_source_ok = False
        readiness = summary.get("recovery_readiness", {})
        if str(readiness.get("status")) not in {"ready", "dirty", "degraded"}:
            git_ready_ok = False

    table.add_row("task-source", "OK" if task_source_ok else "FAIL", "task_source paths parseable")
    if not task_source_ok:
        ok = False

    table.add_row("recovery-readiness", "OK" if git_ready_ok else "FAIL", "git/worktree readiness computed")
    if not git_ready_ok:
        ok = False

    recoverable = orch.list_recoverable_runs()
    table.add_row("recoverable-runs", "OK", str(len(recoverable)))

    stale_worktrees = orch.workspace_root / ".ralphite" / "worktrees"
    stale_count = len(list(stale_worktrees.rglob("*"))) if stale_worktrees.exists() else 0
    table.add_row("worktree-cache", "OK" if stale_count < 200 else "WARN", f"{stale_count} entries under .ralphite/worktrees")

    console.print(table)

    if failures:
        for plan_path, issues, summary in failures:
            console.print(f"\n[bold red]Invalid plan:[/bold red] {plan_path}")
            for issue in issues:
                console.print(f"  - {issue.get('code')}: {issue.get('message')} ({issue.get('path')})")
            if summary:
                console.print(f"  Summary: {summary}")

    return ok


def _print_run_stream(orch: LocalOrchestrator, run_id: str) -> None:
    console.print(f"\n[bold]Streaming run {run_id}[/bold]")
    for event in orch.stream_events(run_id):
        level = event.get("level", "info")
        color = "green" if level == "info" else "yellow" if level == "warn" else "red"
        console.print(f"[{color}]{event['event']}[/{color}] {event['message']}")
        if event["event"] == "RUN_DONE":
            break

    orch.wait_for_run(run_id, timeout=2.0)
    run = orch.get_run(run_id)
    if run and run.artifacts:
        console.print("\nArtifacts:")
        for artifact in run.artifacts:
            console.print(f"- {artifact['id']}: {artifact['path']}")


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
    )
    cfg_path = save_config(orch.workspace_root, config)
    seeded = seed_starter_if_missing(orch.paths["plans"])

    console.print(f"Initialized workspace: [bold]{orch.workspace_root}[/bold]")
    console.print(f"Config: {cfg_path}")
    if seeded:
        console.print(f"Seeded starter plan: {seeded}")
    else:
        console.print("Starter plan already present.")

    strict_ok, strict_messages = _strict_migration_preflight(orch)
    for line in strict_messages:
        console.print(line)
    if not strict_ok:
        raise typer.Exit(code=1)


@app.command()
def doctor(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
) -> None:
    """Check local environment, plans, and runtime readiness."""
    orch = _orchestrator(workspace)
    ok = _doctor_report(orch)
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def run(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    goal: Annotated[str | None, typer.Option(help="Goal text to generate a plan")] = None,
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print streaming logs instead of opening TUI")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Auto-approve requirements")] = False,
    attach_run_detail: Annotated[bool, typer.Option("--attach-run-detail", help="Open phase timeline after start")] = False,
) -> None:
    """Run a plan immediately with optional TUI monitoring."""
    orch = _orchestrator(workspace)

    strict_ok, strict_messages = _strict_migration_preflight(orch)
    for line in strict_messages:
        console.print(line)
    if not strict_ok:
        console.print("[red]Strict migration preflight failed; run blocked.[/red]")
        raise typer.Exit(code=1)

    plan_ref = plan
    if goal:
        generated = orch.goal_to_plan(goal)
        plan_ref = str(generated)
        console.print(f"Generated plan from goal: {generated}")

    requirements = orch.collect_requirements(plan_ref=plan_ref)
    console.print(f"Required tools: {requirements['tools'] or ['none']}")
    console.print(f"Required mcps: {requirements['mcps'] or ['none']}")

    if not yes:
        approved = typer.confirm("Approve these capabilities for this run?", default=True)
        if not approved:
            console.print("Run aborted by user.")
            raise typer.Exit(code=1)

    run_id = orch.start_run(plan_ref=plan_ref, metadata={"source": "cli.run", "goal": goal})
    console.print(f"Started run: [bold]{run_id}[/bold]")

    if no_tui:
        _print_run_stream(orch, run_id)
    else:
        initial_screen = "phase_timeline" if attach_run_detail else "runs"
        AppShell(orchestrator=orch, run_id=run_id, initial_screen=initial_screen).run()


@app.command()
def recover(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    run_id: Annotated[str | None, typer.Option(help="Run id to recover")] = None,
    mode: Annotated[str, typer.Option(help="Recovery mode: manual | agent_best_effort | abort_phase")] = "manual",
    prompt: Annotated[str | None, typer.Option(help="Prompt used by agent_best_effort mode")] = None,
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print stream after recover")] = False,
) -> None:
    """Recover and resume a checkpointed run with explicit recovery mode selection."""
    orch = _orchestrator(workspace)
    target = run_id
    if target is None:
        recoverable = orch.list_recoverable_runs()
        if not recoverable:
            console.print("No recoverable runs found.")
            raise typer.Exit(code=1)
        target = recoverable[-1]

    if not orch.recover_run(target):
        console.print(f"Unable to recover run {target}")
        raise typer.Exit(code=1)

    if not orch.set_recovery_mode(target, mode, prompt=prompt):
        console.print(f"Unable to set recovery mode '{mode}' for {target}")
        raise typer.Exit(code=1)

    if mode != "abort_phase" and not orch.resume_from_checkpoint(target):
        console.print(f"Recovered {target}, but resume failed.")
        if no_tui:
            raise typer.Exit(code=1)

    console.print(f"Recovery mode set for run: [bold]{target}[/bold]")
    if no_tui:
        _print_run_stream(orch, target)
    else:
        AppShell(orchestrator=orch, run_id=target, initial_screen="recovery").run()


@app.command()
def history(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    query: Annotated[str | None, typer.Option(help="Search by id/status/path")] = None,
    limit: Annotated[int, typer.Option(help="Max rows")] = 20,
) -> None:
    """Show local run history."""
    orch = _orchestrator(workspace)
    rows = orch.list_history(limit=limit, query=query)

    table = Table(title="Run History")
    table.add_column("Run ID")
    table.add_column("Status")
    table.add_column("Plan")
    table.add_column("Created")
    table.add_column("Completed")
    for run in rows:
        table.add_row(run.id, run.status, run.plan_path, run.created_at, run.completed_at or "-")
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


@app.command()
def migrate(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    strict: Annotated[bool, typer.Option("--strict", help="Validate in place and block deprecated/invalid plans")] = False,
    to_v2: Annotated[bool, typer.Option("--to-v2", help="Deprecated (conversion removed)")] = False,
) -> None:
    """Validate plan compatibility. Automatic v1 conversion is deprecated and removed."""
    orch = _orchestrator(workspace)

    if to_v2:
        console.print("[yellow]--to-v2 is deprecated. Automatic migration has been removed.[/yellow]")

    if strict or to_v2:
        strict_ok, strict_messages = _strict_migration_preflight(orch)
        for line in strict_messages:
            console.print(line)
        if not strict_ok:
            raise typer.Exit(code=1)
        console.print("[green]Migration compatibility check completed[/green]")
        return

    out_dir = orch.paths["plans"] / "migrated"
    total = 0
    for plan_path in orch.list_plans():
        total += 1
        result = migrate_plan_file(plan_path, out_dir)
        console.print(f"[dim]checked[/dim] {result.source}")
        for warning in result.warnings:
            console.print(f"  - {warning}")

    console.print(f"Migration compatibility check completed: {total} plan(s)")


@app.command()
def check(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    full: Annotated[bool, typer.Option("--full", help="Run full repo test suite")] = False,
) -> None:
    """Run baseline quality gates for local UX reliability."""
    orch = _orchestrator(workspace)
    ok = _doctor_report(orch)
    if not ok:
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
