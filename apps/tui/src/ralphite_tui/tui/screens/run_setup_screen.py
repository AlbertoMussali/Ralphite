from __future__ import annotations

import difflib
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static
import yaml

from ralphite_engine.task_parser import ParsedTask, parse_plan_tasks
from ralphite_engine.templates import versioned_filename
from ralphite_engine.validation import apply_fix, parse_plan_yaml, suggest_fixes, validate_plan_content

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class RunSetupScreen(Vertical):
    DEFAULT_CSS = """
    RunSetupScreen {
      height: 1fr;
      padding: 1;
    }
    #setup-status {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #setup-controls {
      height: auto;
      margin-bottom: 1;
    }
    #setup-plans {
      height: 8;
      margin-bottom: 1;
    }
    #setup-run {
      height: 6;
      margin-bottom: 1;
    }
    #setup-tasks {
      height: 14;
      margin-bottom: 1;
    }
    #setup-validation {
      border: round $warning;
      padding: 1;
      height: 1fr;
    }
    #setup-task-editor {
      height: auto;
      margin-bottom: 1;
    }
    #setup-structure {
      border: round $surface;
      padding: 1;
      margin-bottom: 1;
      height: auto;
    }
    #setup-fix-preview {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
      height: auto;
    }
    .setup-edit-input {
      width: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._plans: list[Path] = []
        self._loaded_plan_path: Path | None = None
        self._loaded_plan_data: dict[str, Any] | None = None
        self._tasks: list[ParsedTask] = []
        self._task_parse_issues: list[str] = []
        self._latest_validation_issues: list[dict[str, Any]] = []
        self._task_badges: dict[int, dict[str, str]] = {}
        self._pending_fixed_plan_data: dict[str, Any] | None = None
        self._pending_fix_diff: str = ""
        self._pending_fix_count: int = 0

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Run Setup (Unified YAML v4)", id="setup-status")
        with Horizontal(id="setup-controls"):
            yield Button("Refresh", id="refresh")
            yield Button("Load Selected", id="load-selected", variant="primary")
            yield Button("Toggle Pre", id="toggle-pre")
            yield Button("Toggle Post", id="toggle-post")
            yield Button("MaxParallel -", id="max-parallel-dec")
            yield Button("MaxParallel +", id="max-parallel-inc")
            yield Button("Validate", id="validate")
            yield Button("Apply Safe Fixes", id="apply-safe-fixes")
            yield Button("Accept Fixes", id="accept-fixes")
            yield Button("Reject Fixes", id="reject-fixes")
            yield Button("Save Revision", id="save-revision", variant="success")
            yield Button("Start", id="start-selected", variant="success")

        plans = DataTable(id="setup-plans")
        plans.add_columns("Plan", "Valid", "Tasks", "Pending", "Parallel", "MaxParallel")
        yield plans

        run_table = DataTable(id="setup-run")
        run_table.add_columns("Control", "Value")
        yield run_table

        tasks = DataTable(id="setup-tasks")
        tasks.add_columns("Task", "Done", "Parallel Group", "Deps", "Agent", "Order", "Title", "Deps✓", "Agent✓", "Group✓")
        yield tasks

        with Horizontal(id="setup-task-editor"):
            yield Input(placeholder="title", id="edit-task-title", classes="setup-edit-input")
            yield Input(placeholder="deps (comma-separated ids)", id="edit-task-deps", classes="setup-edit-input")
            yield Input(placeholder="parallel_group", id="edit-task-group", classes="setup-edit-input")
            yield Input(placeholder="agent id (optional)", id="edit-task-agent", classes="setup-edit-input")
            yield Input(placeholder="completed true|false", id="edit-task-completed", classes="setup-edit-input")
            yield Button("Apply Task Edit", id="apply-task-edit", variant="primary")

        yield Static("No structure preview yet.", id="setup-structure")
        yield Static("No safe-fix preview yet.", id="setup-fix-preview")
        yield Static("Load a plan to edit run controls and preview task blocks.", id="setup-validation")

    def on_mount(self) -> None:
        self._refresh_plans()

    def _status(self) -> Static:
        return self.query_one("#setup-status", Static)

    def _plans_table(self) -> DataTable:
        return self.query_one("#setup-plans", DataTable)

    def _run_table(self) -> DataTable:
        return self.query_one("#setup-run", DataTable)

    def _tasks_table(self) -> DataTable:
        return self.query_one("#setup-tasks", DataTable)

    def _validation(self) -> Static:
        return self.query_one("#setup-validation", Static)

    def _structure(self) -> Static:
        return self.query_one("#setup-structure", Static)

    def _fix_preview(self) -> Static:
        return self.query_one("#setup-fix-preview", Static)

    def _clear_fix_preview(self) -> None:
        self._pending_fixed_plan_data = None
        self._pending_fix_diff = ""
        self._pending_fix_count = 0
        self._fix_preview().update("No safe-fix preview yet.")

    def _task_index_from_issue_path(self, path: str) -> tuple[int | None, str | None]:
        if not path:
            return None, None
        match = re.search(r"tasks\[(\d+)\]", path)
        if not match:
            match = re.search(r"tasks\.(\d+)", path)
        if not match:
            if path == "tasks":
                return -1, "group"
            return None, None
        index = int(match.group(1))
        if ".title" in path:
            return index, "title"
        if ".deps" in path:
            return index, "deps"
        if ".agent" in path:
            return index, "agent"
        if ".parallel_group" in path:
            return index, "group"
        return index, None

    def _rebuild_task_badges(self) -> None:
        self._task_badges = {
            idx: {"title": "OK", "deps": "OK", "agent": "OK", "group": "OK"}
            for idx in range(len(self._tasks))
        }
        for issue in self._latest_validation_issues:
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code", "issue"))
            path = str(issue.get("path", ""))
            index, field = self._task_index_from_issue_path(path)
            if index is None:
                continue
            if index == -1 and field == "group":
                for idx in self._task_badges:
                    self._task_badges[idx]["group"] = f"ERR({code})"
                continue
            if index < 0 or index >= len(self._tasks):
                continue
            target_fields = [field] if field else ["title", "deps", "agent", "group"]
            for target in target_fields:
                self._task_badges[index][target] = f"ERR({code})"

    def _selected_task_index(self) -> int | None:
        table = self._tasks_table()
        row_index = table.cursor_row
        if row_index is None:
            return None
        if row_index < 0 or row_index >= len(self._tasks):
            return None
        return row_index

    def _set_edit_field(self, field_id: str, value: str) -> None:
        self.query_one(f"#{field_id}", Input).value = value

    def _populate_task_editor(self) -> None:
        idx = self._selected_task_index()
        if idx is None:
            return
        task = self._tasks[idx]
        self._set_edit_field("edit-task-title", task.title)
        self._set_edit_field("edit-task-deps", ",".join(task.depends_on))
        self._set_edit_field("edit-task-group", str(task.parallel_group))
        self._set_edit_field("edit-task-agent", task.agent or "")
        self._set_edit_field("edit-task-completed", "true" if task.completed else "false")

    def _selected_plan_path(self) -> Path | None:
        table = self._plans_table()
        row_index = table.cursor_row
        if row_index is None or row_index < 0 or row_index >= len(self._plans):
            return None
        return self._plans[row_index]

    def _refresh_plans(self) -> None:
        self._plans = self.shell.orchestrator.list_plans()
        table = self._plans_table()
        table.clear()

        if not self._plans:
            self._status().update("No plans found under .ralphite/plans")
            return

        for plan_path in self._plans:
            valid, _issues, summary = validate_plan_content(
                plan_path.read_text(encoding="utf-8"),
                workspace_root=self.shell.orchestrator.workspace_root,
            )
            task_counts = summary.get("task_counts", {})
            block_counts = summary.get("block_counts", {})
            table.add_row(
                plan_path.name,
                "yes" if valid else "no",
                str(task_counts.get("total", "-")),
                str(task_counts.get("pending", "-")),
                str(block_counts.get("parallel", "-")),
                str(summary.get("parallel_limit", "-")),
            )

        table.move_cursor(row=0, column=0)
        self._status().update(f"{len(self._plans)} plan(s) discovered. Load one to configure run controls.")

    def _render_editor_tables(self) -> None:
        run_table = self._run_table()
        tasks_table = self._tasks_table()
        run_table.clear()
        tasks_table.clear()

        if not self._loaded_plan_data:
            return

        run_cfg = self._loaded_plan_data.get("run") if isinstance(self._loaded_plan_data.get("run"), dict) else {}
        constraints = (
            self._loaded_plan_data.get("constraints") if isinstance(self._loaded_plan_data.get("constraints"), dict) else {}
        )

        pre = run_cfg.get("pre_orchestrator") if isinstance(run_cfg.get("pre_orchestrator"), dict) else {}
        post = run_cfg.get("post_orchestrator") if isinstance(run_cfg.get("post_orchestrator"), dict) else {}

        run_table.add_row("Pre orchestrator", "on" if bool(pre.get("enabled", False)) else "off")
        run_table.add_row("Pre agent", str(pre.get("agent", "-")))
        run_table.add_row("Post orchestrator", "on" if bool(post.get("enabled", True)) else "off")
        run_table.add_row("Post agent", str(post.get("agent", "-")))
        run_table.add_row("Max parallel", str(constraints.get("max_parallel", 1)))

        for idx, task in enumerate(self._tasks, start=1):
            badges = self._task_badges.get(idx - 1, {"title": "OK", "deps": "OK", "agent": "OK", "group": "OK"})
            tasks_table.add_row(
                task.id,
                "yes" if task.completed else "no",
                str(task.parallel_group),
                ",".join(task.depends_on) or "-",
                task.agent or "worker_default",
                str(idx),
                badges["title"],
                badges["deps"],
                badges["agent"],
                badges["group"],
            )

        if self._tasks:
            tasks_table.move_cursor(row=0, column=0)
            self._populate_task_editor()
        run_table.move_cursor(row=0, column=0)

    def _refresh_validation(self) -> None:
        if not self._loaded_plan_data:
            self._validation().update("Load a plan to validate unified YAML tasks/run/agents.")
            self._structure().update("No structure preview yet.")
            self._latest_validation_issues = []
            self._task_badges = {}
            return

        content = yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False)
        valid, issues, summary = validate_plan_content(content, workspace_root=self.shell.orchestrator.workspace_root)
        self._latest_validation_issues = [issue for issue in issues if isinstance(issue, dict)]
        self._rebuild_task_badges()
        task_counts = summary.get("task_counts", {})
        block_counts = summary.get("block_counts", {})

        lines = [
            f"Plan: {summary.get('plan_id', '-')}",
            f"Valid: {'yes' if valid else 'no'}",
            f"Tasks: total={task_counts.get('total', '-')} pending={task_counts.get('pending', '-')}",
            f"Blocks: sequential={block_counts.get('sequential', '-')} parallel={block_counts.get('parallel', '-')}",
            f"MaxParallel: {summary.get('parallel_limit', '-')}",
            f"Task status: {summary.get('tasks_status', {})}",
        ]
        if self._task_parse_issues:
            lines.append("Task parse issues:")
            lines.extend([f"- {item}" for item in self._task_parse_issues])
        if issues:
            lines.append("Validation issues:")
            lines.extend([f"- {issue.get('code')}: {issue.get('message')} ({issue.get('path')})" for issue in issues])
        self._validation().update("\n".join(lines))
        groups = summary.get("groups", {})
        blocks = summary.get("block_counts", {})
        structure_lines = [
            "Structure Preview:",
            f"- Sequential blocks: {blocks.get('sequential', '-')}",
            f"- Parallel blocks: {blocks.get('parallel', '-')}",
            f"- Max parallel: {summary.get('parallel_limit', '-')}",
        ]
        if isinstance(groups, dict):
            for name, node_ids in groups.items():
                if isinstance(node_ids, list):
                    structure_lines.append(f"- {name}: {len(node_ids)} node(s)")
        self._structure().update("\n".join(structure_lines))
        errored_rows = len(
            [
                idx
                for idx, badges in self._task_badges.items()
                if any(value.startswith("ERR(") for value in badges.values())
            ]
        )
        base = "Validation passed." if valid else f"Validation issues: {len(issues)}."
        self._status().update(f"{base} Task rows with errors: {errored_rows}.")
        self._render_editor_tables()

    def _load_plan(self, path: Path) -> None:
        raw = path.read_text(encoding="utf-8")
        plan_model = parse_plan_yaml(raw)
        self._loaded_plan_data = plan_model.model_dump(mode="json")
        self._loaded_plan_path = path

        tasks, parse_issues = parse_plan_tasks(plan_model)
        self._tasks = tasks
        self._task_parse_issues = parse_issues
        self._clear_fix_preview()

        self._render_editor_tables()
        self._refresh_validation()
        self._status().update(f"Loaded plan {path.name}. You can edit task fields safely and save a validated revision.")

    def _toggle_orchestrator(self, key: str) -> None:
        if not self._loaded_plan_data:
            return
        run_cfg = self._loaded_plan_data.get("run")
        if not isinstance(run_cfg, dict):
            run_cfg = {}
            self._loaded_plan_data["run"] = run_cfg
        block = run_cfg.get(key)
        if not isinstance(block, dict):
            return
        block["enabled"] = not bool(block.get("enabled", False))
        self._clear_fix_preview()
        self._render_editor_tables()
        self._refresh_validation()

    def _change_max_parallel(self, delta: int) -> None:
        if not self._loaded_plan_data:
            return
        constraints = self._loaded_plan_data.get("constraints")
        if not isinstance(constraints, dict):
            constraints = {}
            self._loaded_plan_data["constraints"] = constraints
        current = int(constraints.get("max_parallel", 3) or 3)
        constraints["max_parallel"] = max(1, current + delta)
        self._clear_fix_preview()
        self._render_editor_tables()
        self._refresh_validation()

    def _apply_task_edit(self) -> None:
        if not self._loaded_plan_data:
            self._status().update("Load a plan before editing tasks.")
            return
        idx = self._selected_task_index()
        if idx is None:
            self._status().update("Select a task row to edit.")
            return
        tasks = self._loaded_plan_data.get("tasks")
        if not isinstance(tasks, list) or idx >= len(tasks):
            return
        row = tasks[idx]
        if not isinstance(row, dict):
            return

        title = self.query_one("#edit-task-title", Input).value.strip()
        deps_raw = self.query_one("#edit-task-deps", Input).value.strip()
        group_raw = self.query_one("#edit-task-group", Input).value.strip()
        agent_raw = self.query_one("#edit-task-agent", Input).value.strip()
        completed_raw = self.query_one("#edit-task-completed", Input).value.strip().lower()

        if title:
            row["title"] = title
        row["deps"] = [item.strip() for item in deps_raw.split(",") if item.strip()] if deps_raw else []
        try:
            row["parallel_group"] = max(0, int(group_raw or "0"))
        except ValueError:
            self._status().update("parallel_group must be an integer.")
            return
        row["agent"] = agent_raw or None
        row["completed"] = completed_raw in {"1", "true", "yes", "y"}
        self._clear_fix_preview()

        try:
            model = parse_plan_yaml(yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False))
            self._tasks, self._task_parse_issues = parse_plan_tasks(model)
        except Exception:
            pass
        self._render_editor_tables()
        self._refresh_validation()
        self._status().update(f"Applied edit to task row {idx + 1}.")

    def _apply_safe_fixes(self) -> None:
        if not self._loaded_plan_data:
            return
        content = yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False)
        _valid, issues, _summary = validate_plan_content(content, workspace_root=self.shell.orchestrator.workspace_root)
        fixes = suggest_fixes(self._loaded_plan_data, issues)
        if not fixes:
            self._status().update("No safe fixes available.")
            return
        updated = dict(self._loaded_plan_data)
        for fix in fixes:
            updated = apply_fix(updated, fix)
        before_text = yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False).splitlines()
        after_text = yaml.safe_dump(updated, sort_keys=False, allow_unicode=False).splitlines()
        diff_lines = list(
            difflib.unified_diff(
                before_text,
                after_text,
                fromfile="current_plan",
                tofile="safe_fix_candidate",
                lineterm="",
            )
        )
        self._pending_fixed_plan_data = updated
        self._pending_fix_diff = "\n".join(diff_lines) if diff_lines else "No textual diff generated."
        self._pending_fix_count = len(fixes)
        self._fix_preview().update(
            f"Safe-fix preview ({len(fixes)} fix(es)). Use Accept Fixes or Reject Fixes.\n\n{self._pending_fix_diff}"
        )
        self._status().update(f"Preview ready for {len(fixes)} safe fix(es).")

    def _accept_pending_fixes(self) -> None:
        if self._pending_fixed_plan_data is None:
            self._status().update("No pending safe-fix preview to accept.")
            return
        self._loaded_plan_data = self._pending_fixed_plan_data
        self._pending_fixed_plan_data = None
        applied = self._pending_fix_count
        self._pending_fix_count = 0
        self._pending_fix_diff = ""
        self._fix_preview().update("Safe-fix preview accepted.")
        try:
            model = parse_plan_yaml(yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False))
            self._tasks, self._task_parse_issues = parse_plan_tasks(model)
        except Exception:
            pass
        self._render_editor_tables()
        self._refresh_validation()
        self._status().update(f"Accepted {applied} safe fix(es). Review and save revision.")

    def _reject_pending_fixes(self) -> None:
        if self._pending_fixed_plan_data is None:
            self._status().update("No pending safe-fix preview to reject.")
            return
        self._clear_fix_preview()
        self._status().update("Safe-fix preview rejected.")

    def _save_revision(self) -> Path | None:
        if not self._loaded_plan_data:
            return None

        content = yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False)
        valid, issues, _summary = validate_plan_content(content, workspace_root=self.shell.orchestrator.workspace_root)
        if not valid:
            self._status().update(f"Save blocked by validation issues ({len(issues)}).")
            self._refresh_validation()
            return None

        plan_id = str(self._loaded_plan_data.get("plan_id", "edited-plan"))
        filename = versioned_filename(plan_id, "edited")
        destination = self.shell.orchestrator.paths["plans"] / filename
        destination.write_text(content, encoding="utf-8")
        self._loaded_plan_path = destination
        self._status().update(f"Saved new revision: {destination.name}")
        self._refresh_plans()
        return destination

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button == "refresh":
            self._refresh_plans()
            return
        if button == "load-selected":
            selected = self._selected_plan_path()
            if not selected:
                self._status().update("No plan selected.")
                return
            self._load_plan(selected)
            return
        if button == "toggle-pre":
            self._toggle_orchestrator("pre_orchestrator")
            return
        if button == "toggle-post":
            self._toggle_orchestrator("post_orchestrator")
            return
        if button == "max-parallel-dec":
            self._change_max_parallel(-1)
            return
        if button == "max-parallel-inc":
            self._change_max_parallel(1)
            return
        if button == "validate":
            self._refresh_validation()
            return
        if button == "apply-safe-fixes":
            self._apply_safe_fixes()
            return
        if button == "accept-fixes":
            self._accept_pending_fixes()
            return
        if button == "reject-fixes":
            self._reject_pending_fixes()
            return
        if button == "apply-task-edit":
            self._apply_task_edit()
            return
        if button == "save-revision":
            self._save_revision()
            return
        if button == "start-selected":
            plan_ref: str | None = None
            if self._loaded_plan_data is not None:
                saved = self._save_revision()
                if saved is None:
                    return
                plan_ref = str(saved)
            else:
                selected = self._selected_plan_path()
                plan_ref = str(selected) if selected else None

            if not plan_ref:
                self._status().update("No plan available to start.")
                return

            run_id = self.shell.start_run_for_plan(plan_ref)
            if not run_id:
                self._status().update("Unable to start run for selected plan.")
                return
            self._status().update(f"Started run {run_id}")
            self.shell.show_screen("phase_timeline")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "setup-tasks":
            self._populate_task_editor()
