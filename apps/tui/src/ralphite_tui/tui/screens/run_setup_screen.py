from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static
import yaml

from ralphite_engine.task_parser import ParsedTask, parse_task_file
from ralphite_engine.templates import versioned_filename
from ralphite_engine.validation import parse_plan_yaml, resolve_task_source_path, validate_plan_content

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
    #setup-phases {
      height: 8;
      margin-bottom: 1;
    }
    #setup-tasks {
      height: 12;
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
        self._task_source_path: Path | None = None

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Run Setup (Task-Driven)", id="setup-status")
        with Horizontal(id="setup-controls"):
            yield Button("Refresh", id="refresh")
            yield Button("Load Selected", id="load-selected", variant="primary")
            yield Button("Add Phase", id="add-phase")
            yield Button("Toggle Pre", id="toggle-pre")
            yield Button("Toggle Post", id="toggle-post")
            yield Button("MaxParallel -", id="max-parallel-dec")
            yield Button("MaxParallel +", id="max-parallel-inc")
            yield Button("Task File Hint", id="task-file-hint")
            yield Button("Validate", id="validate")
            yield Button("Save Revision", id="save-revision", variant="success")
            yield Button("Start", id="start-selected", variant="success")

        plans = DataTable(id="setup-plans")
        plans.add_columns("Plan", "Valid", "Phases", "Task Source", "MaxParallel")
        yield plans

        phases = DataTable(id="setup-phases")
        phases.add_columns("Phase", "Pre", "Post")
        yield phases

        tasks = DataTable(id="setup-tasks")
        tasks.add_columns("Task", "Phase", "Lane", "Group", "Deps", "Profile", "Done", "Line")
        yield tasks

        yield Static("Load a plan to preview task-driven execution and controls.", id="setup-validation")

    def on_mount(self) -> None:
        self._refresh_plans()

    def _status(self) -> Static:
        return self.query_one("#setup-status", Static)

    def _plans_table(self) -> DataTable:
        return self.query_one("#setup-plans", DataTable)

    def _phases_table(self) -> DataTable:
        return self.query_one("#setup-phases", DataTable)

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

    def _selected_phase_index(self) -> int | None:
        table = self._phases_table()
        row = table.cursor_row
        if row is None or row < 0:
            return None
        return row

    def _current_phase_rows(self) -> list[dict[str, Any]]:
        if not self._loaded_plan_data:
            return []
        structure = self._loaded_plan_data.get("execution_structure", {})
        if not isinstance(structure, dict):
            return []
        phases = structure.get("phases", [])
        if not isinstance(phases, list):
            return []
        out: list[dict[str, Any]] = []
        for phase in phases:
            if isinstance(phase, dict):
                out.append(phase)
        return out

    def _render_editor_tables(self) -> None:
        phases_table = self._phases_table()
        tasks_table = self._tasks_table()
        phases_table.clear()
        tasks_table.clear()

        phase_rows = self._current_phase_rows()
        for phase in phase_rows:
            pre = phase.get("pre_orchestrator", {}) if isinstance(phase.get("pre_orchestrator"), dict) else {}
            post = phase.get("post_orchestrator", {}) if isinstance(phase.get("post_orchestrator"), dict) else {}
            phases_table.add_row(
                str(phase.get("id", "")),
                "on" if bool(pre.get("enabled", False)) else "off",
                "on" if bool(post.get("enabled", False)) else "off",
            )

        for task in sorted(self._tasks, key=lambda row: row.line_no):
            tasks_table.add_row(
                task.id,
                task.phase,
                task.lane,
                str(task.parallel_group or "-"),
                ",".join(task.depends_on) or "-",
                task.agent_profile,
                "yes" if task.completed else "no",
                str(task.line_no),
            )

        if phase_rows:
            phases_table.move_cursor(row=min(self._selected_phase_index() or 0, len(phase_rows) - 1), column=0)
        if self._tasks:
            tasks_table.move_cursor(row=0, column=0)

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
            task_source = str(summary.get("task_source_status", {}).get("path", "-"))
            table.add_row(
                plan_path.name,
                "yes" if valid else "no",
                str(summary.get("phases", "-")),
                task_source,
                str(summary.get("parallel_limit", "-")),
            )

        table.move_cursor(row=0, column=0)
        self._status().update(f"{len(self._plans)} plan(s) discovered. Load one to configure phase controls.")

    def _refresh_validation(self) -> None:
        if not self._loaded_plan_data:
            self._validation().update("Load a plan to validate task source and execution structure.")
            return

        content = yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False)
        valid, issues, summary = validate_plan_content(content, workspace_root=self.shell.orchestrator.workspace_root)
        max_parallel = (
            self._loaded_plan_data.get("constraints", {}).get("max_parallel", "-")
            if isinstance(self._loaded_plan_data.get("constraints"), dict)
            else "-"
        )
        lines = [
            f"Plan: {summary.get('plan_id', '-')}",
            f"Valid: {'yes' if valid else 'no'}",
            f"Phases: {summary.get('phases', '-')}",
            f"MaxParallel: {max_parallel}",
            f"Task source: {summary.get('task_source_status', {})}",
            f"Lane counts: {summary.get('lane_counts', {})}",
        ]
        if self._task_source_path:
            lines.append(f"Task file: {self._task_source_path}")
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

        task_path = resolve_task_source_path(plan_model.task_source.path, self.shell.orchestrator.workspace_root)
        tasks, parse_issues = parse_task_file(task_path)
        self._task_source_path = task_path
        self._tasks = tasks
        self._task_parse_issues = parse_issues

        self._render_editor_tables()
        self._refresh_validation()
        self._status().update(f"Loaded plan {path.name}. Edit phase controls only; tasks are file-defined.")

    def _toggle_orchestrator(self, key: str) -> None:
        phases = self._current_phase_rows()
        idx = self._selected_phase_index()
        if idx is None or idx >= len(phases):
            return
        block = phases[idx].get(key)
        if not isinstance(block, dict):
            return
        block["enabled"] = not bool(block.get("enabled", False))
        self._render_editor_tables()
        self._refresh_validation()

    def _add_phase(self) -> None:
        if not self._loaded_plan_data:
            return
        phases = self._current_phase_rows()
        existing = {str(phase.get("id", "")) for phase in phases}
        candidate = 1
        while f"phase-{candidate}" in existing:
            candidate += 1
        phases.append(
            {
                "id": f"phase-{candidate}",
                "label": f"Phase {candidate}",
                "pre_orchestrator": {"enabled": False, "agent_profile_id": "orchestrator_pre_default"},
                "post_orchestrator": {"enabled": True, "agent_profile_id": "orchestrator_post_default"},
            }
        )
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
        if button == "add-phase":
            self._add_phase()
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
        if button == "task-file-hint":
            if self._task_source_path:
                self._status().update(f"Edit task file directly: {self._task_source_path}")
            else:
                self._status().update("Load a plan first to view task file path.")
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
