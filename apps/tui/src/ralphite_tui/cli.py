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
    suggest_fixes,
    validate_plan_content,
)
from ralphite_tui.tui.app_shell import AppShell

app = typer.Typer(help="Ralphite terminal-first orchestrator", no_args_is_help=True, add_completion=False)
console = Console()


def _orchestrator(workspace: Path) -> LocalOrchestrator:
    return LocalOrchestrator(workspace.expanduser().resolve())


def _validate_all_plans(orch: LocalOrchestrator) -> tuple[bool, list[tuple[Path, list[dict], list[str]]]]:
    failures: list[tuple[Path, list[dict], list[str]]] = []
    for plan_path in orch.list_plans():
        content = plan_path.read_text(encoding="utf-8")
        valid, issues, _summary = validate_plan_content(content)
        if valid:
            continue
        fix_titles: list[str] = []
        try:
            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict):
                fixes = suggest_fixes(parsed, issues)
                fix_titles = [fix.title for fix in fixes]
        except Exception:  # noqa: BLE001
            pass
        failures.append((plan_path, issues, fix_titles))
    return len(failures) == 0, failures


def _strict_migration_preflight(orch: LocalOrchestrator) -> tuple[bool, list[str]]:
    messages: list[str] = []
    blocked = False
    for plan_path in orch.list_plans():
        result = migrate_plan_in_place(plan_path)
        changed_label = "migrated" if result.changed else "checked"
        messages.append(f"{changed_label} {plan_path.name}")
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

    recoverable = orch.list_recoverable_runs()
    table.add_row("recoverable-runs", "OK", str(len(recoverable)))

    console.print(table)

    if failures:
        for plan_path, issues, fix_titles in failures:
            console.print(f"\n[bold red]Invalid plan:[/bold red] {plan_path}")
            for issue in issues:
                console.print(f"  - {issue.get('code')}: {issue.get('message')} ({issue.get('path')})")
            if fix_titles:
                console.print("  Suggested autofixes:")
                for fix_title in fix_titles:
                    console.print(f"    * {fix_title}")

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
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    profile: Annotated[str | None, typer.Option(help="Profile name for local policy")]=None,
    yes: Annotated[bool, typer.Option("--yes", help="Use defaults without prompts")]=False,
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
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
) -> None:
    """Check local environment, plans, and runtime readiness."""
    orch = _orchestrator(workspace)
    ok = _doctor_report(orch)
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def run(
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")]=None,
    goal: Annotated[str | None, typer.Option(help="Goal text to generate a plan")]=None,
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print streaming logs instead of opening TUI")]=False,
    yes: Annotated[bool, typer.Option("--yes", help="Auto-approve requirements")]=False,
    attach_run_detail: Annotated[bool, typer.Option("--attach-run-detail", help="Open run detail screen after start")]=False,
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
        initial_screen = "run_detail" if attach_run_detail else "runs"
        AppShell(orchestrator=orch, run_id=run_id, initial_screen=initial_screen).run()


@app.command()
def recover(
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    run_id: Annotated[str | None, typer.Option(help="Run id to recover")]=None,
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print stream after recover")]=False,
) -> None:
    """Recover and resume a checkpointed run."""
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

    if not orch.resume_from_checkpoint(target):
        console.print(f"Recovered {target}, but resume failed.")
        raise typer.Exit(code=1)

    console.print(f"Recovered and resumed run: [bold]{target}[/bold]")
    if no_tui:
        _print_run_stream(orch, target)
    else:
        AppShell(orchestrator=orch, run_id=target, initial_screen="run_detail").run()


@app.command()
def history(
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    query: Annotated[str | None, typer.Option(help="Search by id/status/path")]=None,
    limit: Annotated[int, typer.Option(help="Max rows")]=20,
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
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    no_tui: Annotated[bool, typer.Option("--no-tui", help="Print streaming logs instead of opening TUI")]=False,
) -> None:
    """Replay a previous run in rerun-failed mode."""
    orch = _orchestrator(workspace)
    new_run_id = orch.rerun_failed(run_id)
    console.print(f"Replay started: {new_run_id} (from {run_id})")
    if no_tui:
        _print_run_stream(orch, new_run_id)
    else:
        AppShell(orchestrator=orch, run_id=new_run_id, initial_screen="run_detail").run()


@app.command()
def migrate(
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    strict: Annotated[bool, typer.Option("--strict", help="Migrate in place and block invalid plans")]=False,
) -> None:
    """Migrate legacy plans into schema-safe local-first variants."""
    orch = _orchestrator(workspace)

    if strict:
        strict_ok, strict_messages = _strict_migration_preflight(orch)
        for line in strict_messages:
            console.print(line)
        if not strict_ok:
            raise typer.Exit(code=1)
        console.print("[green]Strict migration completed[/green]")
        return

    out_dir = orch.paths["plans"] / "migrated"
    changed = 0
    total = 0
    for plan_path in orch.list_plans():
        total += 1
        result = migrate_plan_file(plan_path, out_dir)
        if result.changed:
            changed += 1
            console.print(f"[green]migrated[/green] {result.source} -> {result.destination}")
        else:
            console.print(f"[dim]unchanged[/dim] {result.source}")
        for warning in result.warnings:
            console.print(f"  - {warning}")

    console.print(f"Migration completed: {changed}/{total} changed")


@app.command()
def check(
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    full: Annotated[bool, typer.Option("--full", help="Run full repo test suite")]=False,
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
    workspace: Annotated[Path, typer.Option(help="Workspace root")]=Path.cwd(),
    screen: Annotated[str, typer.Option(help="Initial screen")]="home",
    run_id: Annotated[str | None, typer.Option(help="Current run id")]=None,
) -> None:
    """Open the Ralphite terminal shell."""
    orch = _orchestrator(workspace)
    AppShell(orchestrator=orch, run_id=run_id, initial_screen=screen).run()


if __name__ == "__main__":
    app()
