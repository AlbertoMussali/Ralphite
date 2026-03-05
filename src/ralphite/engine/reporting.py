from __future__ import annotations

from typing import Any

from ralphite.engine.models import RunViewState
from ralphite.engine.taxonomy import classify_failure


def _node_catalog(run: RunViewState) -> dict[str, dict[str, Any]]:
    resolved = (
        run.metadata.get("resolved_execution", {})
        if isinstance(run.metadata.get("resolved_execution"), dict)
        else {}
    )
    nodes = resolved.get("resolved_nodes") if isinstance(resolved, dict) else []
    catalog: dict[str, dict[str, Any]] = {}
    if not isinstance(nodes, list):
        return catalog
    for item in nodes:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id") or "").strip()
        if node_id:
            catalog[node_id] = item
    return catalog


def _node_sort_key(node_id: str, catalog: dict[str, dict[str, Any]]) -> tuple[int, str]:
    item = catalog.get(node_id, {})
    block_index = int(item.get("block_index", 0) or 0) if isinstance(item, dict) else 0
    return block_index, node_id


def _node_label(node_id: str, catalog: dict[str, dict[str, Any]]) -> str:
    item = catalog.get(node_id, {})
    task_id = (
        str(item.get("source_task_id") or "").strip() if isinstance(item, dict) else ""
    )
    title = str(item.get("task_title") or "").strip() if isinstance(item, dict) else ""
    if task_id and title:
        return f"{task_id} - {title}"
    if task_id:
        return task_id
    if title:
        return title
    return node_id


def _section(title: str, body: list[str]) -> list[str]:
    return [f"## {title}", "", *body, ""]


def _format_duration(metrics: dict[str, Any]) -> str:
    total = metrics.get("total_seconds")
    if isinstance(total, (int, float)):
        return f"{round(float(total), 3)}s"
    return "unknown"


def _format_changed_file(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "M")
    path = str(item.get("path") or "").strip()
    previous_path = str(item.get("previous_path") or "").strip()
    if status.startswith("R") and previous_path:
        return f"{status} {previous_path} -> {path}"
    return f"{status} {path}".strip()


def _build_outcome(run: RunViewState) -> list[str]:
    metrics = run.metadata.get("run_metrics", {})
    metrics = metrics if isinstance(metrics, dict) else {}
    execution = run.metadata.get("execution_defaults", {})
    execution = execution if isinstance(execution, dict) else {}
    status_counts = metrics.get("node_status_counts", {})
    status_counts = status_counts if isinstance(status_counts, dict) else {}
    phase_done = run.metadata.get("phase_done", [])
    phase_done = phase_done if isinstance(phase_done, list) else []
    lines = [
        f"- Status: **{run.status}**",
        f"- Plan: `{run.plan_path}`",
        f"- Duration: {_format_duration(metrics)}",
        f"- Backend: `{execution.get('backend', 'unknown')}`",
        f"- Model: `{execution.get('model', 'unknown')}`",
        f"- Reasoning effort: `{execution.get('reasoning_effort', 'unknown')}`",
        f"- Completed phases: {', '.join(str(item) for item in phase_done) if phase_done else 'none'}",
        f"- Node results: succeeded={int(status_counts.get('succeeded', 0))}, failed={int(status_counts.get('failed', 0))}, blocked={int(status_counts.get('blocked', 0))}",
    ]
    return lines


def _build_changed_files(
    run: RunViewState, catalog: dict[str, dict[str, Any]]
) -> list[str]:
    lines: list[str] = []
    found = False
    for node_id in sorted(
        run.nodes.keys(), key=lambda item: _node_sort_key(item, catalog)
    ):
        state = run.nodes[node_id]
        result = state.result if isinstance(state.result, dict) else {}
        worktree = result.get("worktree") if isinstance(result, dict) else {}
        worktree = worktree if isinstance(worktree, dict) else {}
        changed_files = (
            worktree.get("changed_files")
            if isinstance(worktree.get("changed_files"), list)
            else []
        )
        if not changed_files:
            continue
        found = True
        lines.append(f"### {_node_label(node_id, catalog)}")
        commit = str(worktree.get("commit") or "").strip()
        if commit:
            lines.append(f"- Commit: `{commit}`")
        for item in changed_files:
            if isinstance(item, dict):
                lines.append(f"- {_format_changed_file(item)}")
        lines.append("")

    task_writeback = run.metadata.get("task_writeback", {})
    task_writeback = task_writeback if isinstance(task_writeback, dict) else {}
    writeback = task_writeback.get("task_writeback")
    writeback = writeback if isinstance(writeback, dict) else {}
    writeback_commit = task_writeback.get("task_writeback_commit")
    writeback_commit = writeback_commit if isinstance(writeback_commit, dict) else {}
    if writeback or writeback_commit:
        found = True
        lines.append("### Task write-back")
        commit = str(writeback_commit.get("commit") or "").strip()
        if commit:
            lines.append(f"- Commit: `{commit}`")
        changed_files = (
            writeback_commit.get("changed_files")
            if isinstance(writeback_commit.get("changed_files"), list)
            else []
        )
        if changed_files:
            for item in changed_files:
                if isinstance(item, dict):
                    lines.append(f"- {_format_changed_file(item)}")
        else:
            writeback_paths = (
                writeback_commit.get("paths")
                if isinstance(writeback_commit.get("paths"), list)
                else []
            )
            path = str(
                writeback.get("path") or (writeback_paths[0] if writeback_paths else "")
            )
            if path:
                lines.append(f"- Updated path: `{path}`")
            else:
                mode = (
                    writeback.get("mode") or writeback_commit.get("mode") or "unknown"
                )
                lines.append(f"- Mode: `{mode}`")
        lines.append("")

    if not found:
        return ["- No committed file changes were recorded."]
    return lines[:-1] if lines and lines[-1] == "" else lines


def _acceptance_payload(result: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    acceptance = result.get("acceptance")
    if isinstance(acceptance, dict):
        return acceptance, True
    reason = str(result.get("reason") or "").strip()
    if reason.startswith("acceptance_"):
        return result, False
    return None, True


def _build_acceptance(
    run: RunViewState, catalog: dict[str, dict[str, Any]]
) -> list[str]:
    lines: list[str] = []
    recorded = False
    for node_id in sorted(
        run.nodes.keys(), key=lambda item: _node_sort_key(item, catalog)
    ):
        state = run.nodes[node_id]
        result = state.result if isinstance(state.result, dict) else {}
        acceptance, passed = _acceptance_payload(result)
        if acceptance is None:
            continue
        recorded = True
        lines.append(f"### {_node_label(node_id, catalog)}")
        lines.append(f"- Result: {'PASS' if passed else 'FAIL'}")
        commands = (
            acceptance.get("commands")
            if isinstance(acceptance.get("commands"), list)
            else []
        )
        if commands:
            for command in commands:
                if not isinstance(command, dict):
                    continue
                cmd = str(command.get("command") or "").strip()
                exit_code = command.get("exit_code")
                if isinstance(exit_code, int):
                    status = "PASS" if exit_code == 0 else "FAIL"
                    lines.append(f"- Command {status}: `{cmd}` (exit {exit_code})")
                else:
                    lines.append(f"- Command: `{cmd}`")
        failed_command = str(acceptance.get("failed_command") or "").strip()
        if failed_command:
            lines.append(f"- Failing command: `{failed_command}`")
        artifacts = (
            acceptance.get("required_artifacts")
            if isinstance(acceptance.get("required_artifacts"), list)
            else []
        )
        if artifacts:
            for item in artifacts:
                if not isinstance(item, dict):
                    continue
                artifact_id = str(item.get("id") or "artifact")
                matches = (
                    item.get("matches") if isinstance(item.get("matches"), list) else []
                )
                if matches:
                    lines.append(f"- Artifact PASS: `{artifact_id}`")
                    for match in matches:
                        lines.append(f"  - `{match}`")
                else:
                    lines.append(f"- Artifact FAIL: `{artifact_id}`")
        missing_artifact = str(acceptance.get("missing_artifact") or "").strip()
        if missing_artifact:
            lines.append(f"- Missing artifact: `{missing_artifact}`")
        rubric = (
            acceptance.get("rubric")
            if isinstance(acceptance.get("rubric"), list)
            else []
        )
        for item in rubric:
            if isinstance(item, str) and item.strip():
                lines.append(f"- Rubric: {item.strip()}")
        lines.append("")
    if not recorded:
        return [
            "- No task-level acceptance commands or required artifacts were defined."
        ]
    return lines[:-1] if lines and lines[-1] == "" else lines


def _build_failures(run: RunViewState, catalog: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for node_id in sorted(
        run.nodes.keys(), key=lambda item: _node_sort_key(item, catalog)
    ):
        state = run.nodes[node_id]
        result = state.result if isinstance(state.result, dict) else {}
        if state.status != "failed" or not result:
            continue
        title = str(result.get("failure_title") or "").strip()
        reason = str(result.get("reason") or "runtime_error").strip()
        details = str(result.get("error") or result.get("message") or "").strip()
        prefix = title or classify_failure(reason).title
        lines.append(f"- {_node_label(node_id, catalog)}: {prefix} (`{reason}`)")
        if details:
            lines.append(f"  - {details}")

    recovery = run.metadata.get("recovery", {})
    recovery = recovery if isinstance(recovery, dict) else {}
    details = (
        recovery.get("details") if isinstance(recovery.get("details"), dict) else {}
    )
    conflicts = (
        details.get("conflict_files")
        if isinstance(details.get("conflict_files"), list)
        else []
    )
    for item in conflicts:
        lines.append(f"- Unresolved conflict: `{item}`")

    for event in run.events:
        if str(event.get("event")) == "TASK_WRITEBACK_FAILED":
            meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
            reason = str(meta.get("reason") or "task_writeback_failed")
            title = classify_failure(reason).title
            lines.append(f"- Task write-back: {title} (`{reason}`)")
        if str(event.get("event")) != "CLEANUP_DONE":
            continue
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        items = meta.get("items") if isinstance(meta.get("items"), list) else []
        for item in items:
            text = str(item)
            if "skipped" in text:
                lines.append(f"- Cleanup warning: {text}")

    if not lines:
        return ["- No failures or warnings recorded."]
    return lines


def _build_next_steps(run: RunViewState) -> list[str]:
    actions: list[str] = []
    for state in run.nodes.values():
        result = state.result if isinstance(state.result, dict) else {}
        for key in ("next_action", "command_hint"):
            value = str(result.get(key) or "").strip()
            if value:
                actions.append(value)

    recovery = run.metadata.get("recovery", {})
    recovery = recovery if isinstance(recovery, dict) else {}
    details = (
        recovery.get("details") if isinstance(recovery.get("details"), dict) else {}
    )
    next_commands = (
        details.get("next_commands")
        if isinstance(details.get("next_commands"), list)
        else []
    )
    actions.extend(str(item).strip() for item in next_commands if str(item).strip())

    for event in run.events:
        if str(event.get("event")) != "TASK_WRITEBACK_FAILED":
            continue
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        advice = classify_failure(str(meta.get("reason") or "task_writeback_failed"))
        actions.extend([advice.next_action, advice.command_hint])

    if not actions and run.status == "succeeded":
        actions.extend(
            [
                "Review the changed files and acceptance results in this report.",
                "Inspect machine_bundle.json if you need machine-readable node details.",
            ]
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for item in actions:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return [f"- {item}" for item in deduped] or ["- No follow-up action recorded."]


def _build_supporting_artifacts(
    artifact_paths: dict[str, str], run_state_paths: dict[str, str]
) -> list[str]:
    labels = {
        "final_report": "Human summary",
        "run_metrics": "Run metrics",
        "machine_bundle": "Machine bundle",
        "run_state": "Run state",
        "checkpoint": "Checkpoint",
        "event_log": "Event log",
    }
    lines: list[str] = []
    for key in (
        "final_report",
        "run_metrics",
        "machine_bundle",
        "run_state",
        "checkpoint",
        "event_log",
    ):
        path = artifact_paths.get(key) or run_state_paths.get(key)
        if not path:
            continue
        lines.append(f"- {labels[key]}: `{path}`")
    return lines


def _build_highlights(run: RunViewState) -> list[str]:
    keep = {
        "RUN_STARTED",
        "PHASE_STARTED",
        "WORKER_MERGED",
        "ORCH_DONE",
        "PHASE_DONE",
        "RECOVERY_REQUIRED",
        "RECOVERY_MODE_SELECTED",
        "RECOVERY_PREFLIGHT_FAILED",
        "RECOVERY_RESUMED",
        "CLEANUP_DONE",
        "TASK_WRITEBACK_FAILED",
        "RUN_DONE",
    }
    lines: list[str] = []
    for event in run.events:
        event_name = str(event.get("event") or "")
        if event_name not in keep:
            continue
        ts = str(event.get("ts") or "")
        message = str(event.get("message") or "").strip()
        if ts:
            lines.append(f"- {ts} {event_name}: {message}")
        else:
            lines.append(f"- {event_name}: {message}")
    return lines or ["- No notable milestones were recorded."]


def build_final_report(
    run: RunViewState,
    *,
    artifact_paths: dict[str, str],
    run_state_paths: dict[str, str],
) -> str:
    catalog = _node_catalog(run)
    sections: list[str] = [
        f"# Run {run.id} Summary",
        "",
        *_section("Outcome", _build_outcome(run)),
        *_section("Changed Files", _build_changed_files(run, catalog)),
        *_section("Acceptance Results", _build_acceptance(run, catalog)),
        *_section("Failures and Warnings", _build_failures(run, catalog)),
        *_section("Next Steps", _build_next_steps(run)),
        *_section(
            "Supporting Artifacts",
            _build_supporting_artifacts(artifact_paths, run_state_paths),
        ),
        *_section("Run Highlights", _build_highlights(run)),
    ]
    return "\n".join(sections).strip() + "\n"
