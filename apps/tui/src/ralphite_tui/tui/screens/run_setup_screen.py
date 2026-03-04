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
      height: 10;
      margin-bottom: 1;
    }
    #setup-phases {
      height: 10;
      margin-bottom: 1;
    }
    #setup-tasks {
      height: 10;
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

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Run Setup + Phase Editor", id="setup-status")
        with Horizontal(id="setup-controls"):
            yield Button("Refresh", id="refresh")
            yield Button("Load Selected", id="load-selected", variant="primary")
            yield Button("Add Phase", id="add-phase")
            yield Button("Toggle Pre", id="toggle-pre")
            yield Button("Toggle Post", id="toggle-post")
            yield Button("Cycle Lane", id="cycle-lane")
            yield Button("Validate", id="validate")
            yield Button("Save Revision", id="save-revision", variant="success")
            yield Button("Start", id="start-selected", variant="success")

        plans = DataTable(id="setup-plans")
        plans.add_columns("Plan", "Valid", "Phases", "Task Source")
        yield plans

        phases = DataTable(id="setup-phases")
        phases.add_columns("Phase", "Pre", "Seq Pre", "Parallel", "Seq Post", "Post")
        yield phases

        tasks = DataTable(id="setup-tasks")
        tasks.add_columns("Task", "Meta Phase", "Meta Lane", "Done", "Selected Phase Lane")
        yield tasks

        yield Static("Load a plan to edit execution structure.", id="setup-validation")

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

    def _selected_task(self) -> ParsedTask | None:
        table = self._tasks_table()
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._tasks):
            return None
        return self._tasks[row]

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
            table.add_row(plan_path.name, "yes" if valid else "no", str(summary.get("phases", "-")), task_source)

        table.move_cursor(row=0, column=0)
        self._status().update(f"{len(self._plans)} plan(s) discovered. Load one to edit phases.")

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

    def _task_lane_for_phase(self, phase: dict[str, Any], task_id: str) -> str:
        workers = phase.get("workers", {}) if isinstance(phase.get("workers"), dict) else {}
        for lane in ("sequential_before", "parallel", "sequential_after"):
            lane_values = workers.get(lane, [])
            if isinstance(lane_values, list) and task_id in lane_values:
                return {"sequential_before": "seq_pre", "parallel": "parallel", "sequential_after": "seq_post"}[lane]
        return "none"

    def _render_editor_tables(self) -> None:
        phases_table = self._phases_table()
        tasks_table = self._tasks_table()
        phases_table.clear()
        tasks_table.clear()

        phase_rows = self._current_phase_rows()
        for phase in phase_rows:
            workers = phase.get("workers", {}) if isinstance(phase.get("workers"), dict) else {}
            seq_pre = ",".join(workers.get("sequential_before", [])) if isinstance(workers.get("sequential_before"), list) else ""
            parallel = ",".join(workers.get("parallel", [])) if isinstance(workers.get("parallel"), list) else ""
            seq_post = ",".join(workers.get("sequential_after", [])) if isinstance(workers.get("sequential_after"), list) else ""
            pre = phase.get("pre_orchestrator", {}) if isinstance(phase.get("pre_orchestrator"), dict) else {}
            post = phase.get("post_orchestrator", {}) if isinstance(phase.get("post_orchestrator"), dict) else {}
            phases_table.add_row(
                str(phase.get("id", "")),
                "on" if bool(pre.get("enabled", False)) else "off",
                seq_pre,
                parallel,
                seq_post,
                "on" if bool(post.get("enabled", False)) else "off",
            )

        selected_phase = phase_rows[self._selected_phase_index() or 0] if phase_rows else None
        for task in self._tasks:
            selected_lane = self._task_lane_for_phase(selected_phase, task.id) if selected_phase else "none"
            tasks_table.add_row(
                task.id,
                task.phase,
                task.lane,
                "yes" if task.completed else "no",
                selected_lane,
            )

        if phase_rows:
            phases_table.move_cursor(row=min(self._selected_phase_index() or 0, len(phase_rows) - 1), column=0)
        if self._tasks:
            tasks_table.move_cursor(row=min(tasks_table.cursor_row or 0, len(self._tasks) - 1), column=0)

    def _refresh_validation(self) -> None:
        if not self._loaded_plan_data:
            self._validation().update("Load a plan to validate edited execution structure.")
            return
        content = yaml.safe_dump(self._loaded_plan_data, sort_keys=False, allow_unicode=False)
        valid, issues, summary = validate_plan_content(content, workspace_root=self.shell.orchestrator.workspace_root)
        lines = [
            f"Plan: {summary.get('plan_id', '-')}",
            f"Valid: {'yes' if valid else 'no'}",
            f"Phases: {summary.get('phases', '-')}",
            f"Lane counts: {summary.get('lane_counts', {})}",
            f"Task source: {summary.get('task_source_status', {})}",
        ]
        if issues:
            lines.append("Issues:")
            lines.extend([f"- {issue.get('code')}: {issue.get('message')} ({issue.get('path')})" for issue in issues])
        self._validation().update("\n".join(lines))

    def _load_plan(self, path: Path) -> None:
        raw = path.read_text(encoding="utf-8")
        plan_model = parse_plan_yaml(raw)
        self._loaded_plan_data = plan_model.model_dump(mode="json")
        self._loaded_plan_path = path

        task_path = resolve_task_source_path(plan_model.task_source.path, self.shell.orchestrator.workspace_root)
        tasks, _parse_issues = parse_task_file(task_path)
        self._tasks = [task for task in tasks if not task.completed]

        self._render_editor_tables()
        self._refresh_validation()
        self._status().update(f"Loaded plan {path.name}. Edit phases/lane assignment and save revision.")

    def _toggle_orchestrator(self, key: str) -> None:
        phases = self._current_phase_rows()
        idx = self._selected_phase_index()
        if idx is None or idx >= len(phases):
            return
        phase = phases[idx]
        block = phase.get(key)
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
                "workers": {"sequential_before": [], "parallel": [], "sequential_after": []},
                "post_orchestrator": {"enabled": True, "agent_profile_id": "orchestrator_post_default"},
            }
        )
        self._render_editor_tables()
        self._refresh_validation()

    def _cycle_selected_task_lane(self) -> None:
        phases = self._current_phase_rows()
        idx = self._selected_phase_index()
        task = self._selected_task()
        if idx is None or idx >= len(phases) or task is None:
            return

        phase = phases[idx]
        workers = phase.get("workers") if isinstance(phase.get("workers"), dict) else None
        if workers is None:
            return

        lane_map = {
            "seq_pre": "sequential_before",
            "parallel": "parallel",
            "seq_post": "sequential_after",
            "none": "none",
        }
        current = self._task_lane_for_phase(phase, task.id)
        order = ["none", "seq_pre", "parallel", "seq_post"]
        next_lane = order[(order.index(current) + 1) % len(order)]

        for lane_key in ("sequential_before", "parallel", "sequential_after"):
            lane_values = workers.get(lane_key, [])
            if not isinstance(lane_values, list):
                lane_values = []
            workers[lane_key] = [item for item in lane_values if item != task.id]

        if next_lane != "none":
            target_key = lane_map[next_lane]
            lane_values = workers.get(target_key, [])
            if not isinstance(lane_values, list):
                lane_values = []
            if task.id not in lane_values:
                lane_values.append(task.id)
            workers[target_key] = lane_values

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

        if button == "add-phase":
            self._add_phase()
            return

        if button == "toggle-pre":
            self._toggle_orchestrator("pre_orchestrator")
            return

        if button == "toggle-post":
            self._toggle_orchestrator("post_orchestrator")
            return

        if button == "cycle-lane":
            self._cycle_selected_task_lane()
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
