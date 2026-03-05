from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from rich.table import Table
import yaml

from ralphite.engine import LocalOrchestrator, suggest_fixes, validate_plan_content
from ralphite.engine.headless_agent import build_codex_exec_command

from .core import console


def _validate_all_plans(
    orch: LocalOrchestrator,
) -> tuple[bool, list[tuple[Path, list[dict], dict]]]:
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


def _probe_codex_model(model: str, reasoning_effort: str) -> tuple[bool, str]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True, "skipped in pytest"
    if os.getenv("RALPHITE_SKIP_MODEL_PROBE") == "1":
        return True, "skipped by RALPHITE_SKIP_MODEL_PROBE"
    if not shutil.which("codex"):
        return False, "codex not found"
    command = build_codex_exec_command(
        prompt="Reply with exactly: OK",
        model=model,
        reasoning_effort=reasoning_effort,
        sandbox="read-only",
    )
    try:
        run = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=25
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

    errors: list[str] = []
    for line in (run.stdout or "").splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        ptype = str(payload.get("type", ""))
        if ptype == "error":
            msg = payload.get("message")
            if isinstance(msg, str) and msg.strip():
                errors.append(msg.strip())
        elif ptype == "turn.failed":
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            msg = err.get("message")
            if isinstance(msg, str) and msg.strip():
                errors.append(msg.strip())
        elif ptype == "item.completed":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if str(item.get("type")) == "error":
                msg = item.get("message")
                if isinstance(msg, str) and msg.strip():
                    errors.append(msg.strip())

    if run.returncode != 0:
        detail = (run.stderr or run.stdout or "").strip() or f"exit={run.returncode}"
        return False, detail
    if errors:
        return False, errors[0]
    return True, "model probe succeeded"


def _doctor_snapshot(
    orch: LocalOrchestrator, include_fix_suggestions: bool = False
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True
    test_mode = bool(os.getenv("PYTEST_CURRENT_TEST"))

    required_cmds = ["python3", "uv", "git", "rg"]
    for cmd in required_cmds:
        found = shutil.which(cmd)
        if found:
            status = "OK"
        elif test_mode and cmd == "rg":
            status = "WARN"
        else:
            status = "MISSING"
            ok = False
        checks.append(
            {"check": f"cmd:{cmd}", "status": status, "detail": found or "not in PATH"}
        )

    default_backend = str(orch.config.default_backend or "codex").strip().lower()
    skip_backend_checks = os.getenv("RALPHITE_SKIP_BACKEND_CMD_CHECKS") == "1"
    codex_required = default_backend == "codex"
    cursor_required = default_backend == "cursor"

    codex_path = shutil.which("codex")
    codex_status = (
        "OK"
        if codex_path
        else (
            "WARN"
            if (test_mode or skip_backend_checks)
            else ("MISSING" if codex_required else "WARN")
        )
    )
    checks.append(
        {
            "check": "cmd:codex",
            "status": codex_status,
            "detail": codex_path or "not in PATH",
        }
    )
    if codex_required and not codex_path and not (test_mode or skip_backend_checks):
        ok = False

    cursor_command = str(orch.config.cursor_command or "agent").strip() or "agent"
    cursor_path = shutil.which(cursor_command)
    cursor_status = (
        "OK"
        if cursor_path
        else (
            "WARN"
            if (test_mode or skip_backend_checks)
            else ("MISSING" if cursor_required else "WARN")
        )
    )
    checks.append(
        {
            "check": f"cmd:{cursor_command}",
            "status": cursor_status,
            "detail": cursor_path or "not in PATH",
        }
    )
    if cursor_required and not cursor_path and not (test_mode or skip_backend_checks):
        ok = False

    if codex_required and codex_path and not skip_backend_checks:
        model_ok, model_detail = _probe_codex_model(
            str(orch.config.default_model or "gpt-5.3-codex"),
            str(orch.config.default_reasoning_effort or "medium"),
        )
        checks.append(
            {
                "check": "codex-model-probe",
                "status": "OK" if model_ok else "WARN",
                "detail": model_detail,
            }
        )

    cfg_path = orch.paths["config"]
    cfg_ok = cfg_path.exists()
    checks.append(
        {
            "check": "config",
            "status": "OK" if cfg_ok else "MISSING",
            "detail": str(cfg_path),
        }
    )
    if not cfg_ok:
        ok = False

    plans = orch.list_plans()
    checks.append(
        {
            "check": "plans",
            "status": "OK" if plans else "MISSING",
            "detail": f"{len(plans)} plan file(s)",
        }
    )
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

    checks.append(
        {
            "check": "tasks",
            "status": "OK" if tasks_ok else "FAIL",
            "detail": "embedded YAML tasks parseable",
        }
    )
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
    checks.append(
        {"check": "recoverable-runs", "status": "OK", "detail": str(len(recoverable))}
    )

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


def _doctor_evaluation(
    snapshot: dict[str, Any], *, strict: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    non_critical = {"stale-artifacts", "recovery-readiness", "codex-model-probe"}
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
    return list(dict.fromkeys(commands))


def _render_doctor_table(snapshot: dict[str, Any]) -> None:
    table = Table(title="Ralphite Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for row in snapshot.get("checks", []):
        if not isinstance(row, dict):
            continue
        table.add_row(
            str(row.get("check", "")),
            str(row.get("status", "")),
            str(row.get("detail", "")),
        )
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
                console.print(
                    f"  - {issue.get('code')}: {issue.get('message')} ({issue.get('path')})"
                )
        if summary:
            console.print(f"  Summary: {summary}")
        recommended = (
            summary.get("recommended_commands", []) if isinstance(summary, dict) else []
        )
        if isinstance(recommended, list) and recommended:
            console.print("  Recommended commands:")
            for cmd in recommended:
                if isinstance(cmd, str):
                    console.print(f"  - {cmd}")

    stale = snapshot.get("stale_artifacts", {})
    if not isinstance(stale, dict):
        return
    stale_worktrees = (
        stale.get("stale_worktrees", [])
        if isinstance(stale.get("stale_worktrees"), list)
        else []
    )
    stale_branches = (
        stale.get("stale_branches", [])
        if isinstance(stale.get("stale_branches"), list)
        else []
    )
    if stale_worktrees or stale_branches:
        console.print("\n[bold yellow]Stale managed artifacts[/bold yellow]")
        for item in stale_worktrees[:10]:
            if not isinstance(item, dict):
                continue
            console.print(
                f"  - worktree run={item.get('run_id')} age={item.get('age_hours')}h path={item.get('path')}"
            )
        for item in stale_branches[:10]:
            if not isinstance(item, dict):
                continue
            console.print(
                f"  - branch run={item.get('run_id')} branch={item.get('branch')}"
            )
        console.print(
            "  Action: run cleanup by resolving or resuming stale runs, then re-check doctor."
        )
