from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ralphite_engine import LocalConfig, save_config, seed_starter_if_missing

from ..core import (
    _bootstrap_plan_file,
    _find_first_valid_plan,
    _orchestrator,
    _parse_csv_items,
    console,
)


def init_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    profile: Annotated[
        str | None, typer.Option(help="Profile name for local policy")
    ] = None,
    template: Annotated[
        str,
        typer.Option(
            "--template",
            help="Bootstrap template: general_sps | branched | blue_red | custom",
        ),
    ] = "general_sps",
    plan_id: Annotated[
        str | None, typer.Option(help="Optional plan_id for generated bootstrap plan")
    ] = None,
    name: Annotated[
        str | None, typer.Option(help="Optional name for generated bootstrap plan")
    ] = None,
    goal: Annotated[
        str | None, typer.Option(help="Optional goal text for bootstrap plan tasks")
    ] = None,
    branched_lanes: Annotated[
        str | None, typer.Option(help="Comma-separated branched lane names")
    ] = None,
    blue_red_loop_unit: Annotated[
        str, typer.Option(help="blue_red.loop_unit value")
    ] = "per_task",
    yes: Annotated[
        bool, typer.Option("--yes", help="Use defaults without prompts")
    ] = False,
) -> None:
    """Initialize a local-first Ralphite workspace and bootstrap a v1 plan."""
    orch = _orchestrator(workspace, bootstrap=False)

    profile_name = profile or orch.config.profile_name
    if not yes and profile is None:
        profile_name = typer.prompt("Profile name", default=orch.config.profile_name)
    effective_template = (template or "general_sps").strip()
    if not yes and template == "general_sps":
        effective_template = (
            typer.prompt("Template", default="general_sps").strip() or "general_sps"
        )
    allowed_templates = {"general_sps", "branched", "blue_red", "custom"}
    if effective_template not in allowed_templates:
        raise typer.BadParameter(
            f"template must be one of: {', '.join(sorted(allowed_templates))}"
        )

    effective_plan_id = (plan_id or "starter_loop").strip() or "starter_loop"
    effective_name = (name or "Starter Loop").strip() or "Starter Loop"
    if not yes and plan_id is None:
        effective_plan_id = (
            typer.prompt("Plan ID", default=effective_plan_id).strip()
            or effective_plan_id
        )
    if not yes and name is None:
        effective_name = (
            typer.prompt("Plan name", default=effective_name).strip() or effective_name
        )
    lanes = _parse_csv_items(branched_lanes, default=["lane_a", "lane_b"])
    if not yes and effective_template == "branched" and branched_lanes is None:
        lane_prompt = typer.prompt(
            "Branched lanes (comma-separated)", default="lane_a,lane_b"
        )
        lanes = _parse_csv_items(lane_prompt, default=lanes)

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
        default_backend=orch.config.default_backend,
        default_model=orch.config.default_model,
        default_reasoning_effort=orch.config.default_reasoning_effort,
        cursor_command=orch.config.cursor_command,
    )
    cfg_path = save_config(orch.workspace_root, config)
    seeded = seed_starter_if_missing(orch.paths["plans"])

    reused_plan = _find_first_valid_plan(orch)
    create_new = (
        reused_plan is None
        or goal is not None
        or plan_id is not None
        or name is not None
        or template != "general_sps"
        or branched_lanes is not None
        or blue_red_loop_unit != "per_task"
    )
    generated_plan: Path | None = None
    if create_new:
        generated_plan = _bootstrap_plan_file(
            orch,
            template=effective_template,
            goal=goal,
            plan_id=effective_plan_id,
            name=effective_name,
            lanes=lanes,
            loop_unit=blue_red_loop_unit,
        )
    selected_plan = generated_plan or reused_plan or seeded

    console.print(f"Initialized workspace: [bold]{orch.workspace_root}[/bold]")
    console.print(f"Config: {cfg_path}")
    if generated_plan:
        console.print(f"Generated bootstrap plan: {generated_plan}")
    elif selected_plan:
        console.print(f"Bootstrap plan: {selected_plan}")
    else:
        console.print("Bootstrap plan already present.")
