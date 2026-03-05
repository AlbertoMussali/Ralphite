from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from ralphite_engine import apply_fix, suggest_fixes, validate_plan_content

from ..core import _emit_payload, _normalize_output, _orchestrator, _resolve_plan_ref, _result_payload, console


def validate_command(
    workspace: Annotated[Path, typer.Option(help="Workspace root")] = Path.cwd(),
    plan: Annotated[str | None, typer.Option(help="Plan file path or name")] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Emit machine-readable output")] = False,
    apply_safe_fixes: Annotated[
        bool, typer.Option("--apply-safe-fixes", help="Write an auto-fixed revision")
    ] = False,
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
