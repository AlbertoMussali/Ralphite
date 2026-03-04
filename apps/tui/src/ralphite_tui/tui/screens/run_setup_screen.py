from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static
import yaml

from ralphite_engine.task_parser import ParsedTask, parse_plan_tasks
from ralphite_engine.templates import versioned_filename
from ralphite_engine.validation import parse_plan_yaml, validate_plan_content

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
    """

    def __init__(self) -> None:
        super().__init__()
        self._plans: list[Path] = []
        self._loaded_plan_path: Path | None = None
        self._loaded_plan_data: dict[str, Any] | None = None
        self._tasks: list[ParsedTask] = []
        self._task_parse_issues: list[str] = []

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
            yield Button("Save Revision", id="save-revision", variant="success")
            yield Button("Start", id="start-selected", variant="success")

        plans = DataTable(id="setup-plans")
        plans.add_columns("Plan", "Valid", "Tasks", "Pending", "Parallel", "MaxParallel")
        yield plans

        run_table = DataTable(id="setup-run")
        run_table.add_columns("Control", "Value")
        yield run_table

        tasks = DataTable(id="setup-tasks")
        tasks.add_columns("Task", "Done", "Parallel Group", "Deps", "Agent", "Order")
        yield tasks

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
            tasks_table.add_row(
                task.id,
                "yes" if task.completed else "no",
                str(task.parallel_group),
                ",".join(task.depends_on) or "-",
                task.agent_profile,
                str(idx),
            )

        if self._tasks:
            tasks_table.move_cursor(row=0, column=0)
        run_table.move_cursor(row=0, column=0)

    def _refresh_validation(self) -> None:
        if not self._loaded_plan_data:
            self._validation().update("Load a plan to validate unified YAML tasks/run/agents.")
            return

        content = yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False)
        valid, issues, summary = validate_plan_content(content, workspace_root=self.shell.orchestrator.workspace_root)
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

    def _load_plan(self, path: Path) -> None:
        raw = path.read_text(encoding="utf-8")
        plan_model = parse_plan_yaml(raw)
        self._loaded_plan_data = plan_model.model_dump(mode="json")
        self._loaded_plan_path = path

        tasks, parse_issues = parse_plan_tasks(plan_model)
        self._tasks = tasks
        self._task_parse_issues = parse_issues

        self._render_editor_tables()
        self._refresh_validation()
        self._status().update(f"Loaded plan {path.name}. Task order is read-only and defined in tasks list.")

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
        self._render_editor_tables()
        self._refresh_validation()

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
