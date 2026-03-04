from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static
import yaml

from ralphite_engine import (
    EditorSessionState,
    PlanDraftState,
    apply_fix,
    autosave_snapshot,
    latest_snapshot,
    parse_plan_yaml,
    plan_to_rows,
    rows_to_plan_data,
    split_csv,
    suggest_fixes,
    validate_plan_content,
)
from ralphite_engine.templates import versioned_filename

if TYPE_CHECKING:
    from ralphite_tui.tui.app_shell import AppShell


class FieldEditModal(ModalScreen[tuple[str, str] | None]):
    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    CSS = """
    FieldEditModal {
      align: center middle;
    }
    #editor-field-modal {
      width: 80;
      border: round $accent;
      background: $panel;
      padding: 1;
    }
    #editor-field-actions {
      margin-top: 1;
      height: auto;
    }
    """

    def __init__(self, field: str, value: str) -> None:
        super().__init__()
        self.initial_field = field
        self.initial_value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="editor-field-modal"):
            yield Static("Edit Field")
            yield Input(value=self.initial_field, id="field-name")
            yield Input(value=self.initial_value, id="field-value")
            with Horizontal(id="editor-field-actions"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            field = self.query_one("#field-name", Input).value.strip()
            value = self.query_one("#field-value", Input).value
            self.dismiss((field, value))
        else:
            self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class EditorScreen(Vertical):
    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("a", "add_row", "Add"),
        ("d", "delete_row", "Delete"),
        ("e", "edit_field", "Edit"),
        ("ctrl+s", "save", "Save"),
        ("ctrl+v", "validate", "Validate"),
        ("ctrl+f", "apply_fix", "Apply Fix"),
        ("ctrl+d", "save_draft_only", "Draft Save"),
        ("tab", "panel_next", "Next Panel"),
        ("shift+tab", "panel_prev", "Prev Panel"),
    ]

    DEFAULT_CSS = """
    EditorScreen {
      height: 1fr;
      padding: 1;
    }
    #editor-status {
      border: round $accent;
      padding: 1;
      margin-bottom: 1;
    }
    #editor-panels {
      height: auto;
      margin-bottom: 1;
    }
    #editor-rows {
      height: 1fr;
      margin-bottom: 1;
    }
    #editor-form {
      border: round $surface;
      padding: 1;
      margin-bottom: 1;
      height: auto;
    }
    #editor-diagnostics {
      border: round $warning;
      padding: 1;
      height: auto;
    }
    """

    PANEL_ORDER = ["steps", "edges", "loops", "agents", "constraints", "outputs"]

    def __init__(self, plan_path: Path | None = None) -> None:
        super().__init__()
        self.plan_path = plan_path
        self.session = EditorSessionState(draft_id="draft", plan_path="", selected_panel="steps")
        self.buffer: dict[str, Any] = {}
        self.issues: list[dict[str, Any]] = []
        self.fixes: list[Any] = []

    @property
    def shell(self) -> "AppShell":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("Loading editor...", id="editor-status")
        with Horizontal(id="editor-panels"):
            for panel in self.PANEL_ORDER:
                yield Button(panel.title(), id=f"panel-{panel}")
        rows = DataTable(id="editor-rows")
        yield rows
        yield Static("Select a row to inspect fields.", id="editor-form")
        yield Static("Run ctrl+v to validate.", id="editor-diagnostics")

    def on_mount(self) -> None:
        self._load_plan()
        self.set_interval(8.0, self._autosave_tick)
        self._render_all()

    def _load_plan(self) -> None:
        if self.plan_path is None:
            plans = self.shell.orchestrator.list_plans()
            if not plans:
                raise ValueError("No plans found for editor")
            self.plan_path = plans[0]

        raw = self.plan_path.read_text(encoding="utf-8")
        draft_id = self.plan_path.stem
        snapshot = latest_snapshot(self.shell.orchestrator.paths["drafts"], draft_id=draft_id)
        if snapshot and snapshot.exists():
            raw = snapshot.read_text(encoding="utf-8")

        plan = parse_plan_yaml(raw)
        self.buffer = plan_to_rows(plan)
        self.session = EditorSessionState(draft_id=draft_id, plan_path=str(self.plan_path), selected_panel="steps")

    def _panel_rows(self, panel: str | None = None) -> list[Any]:
        panel_name = panel or self.session.selected_panel
        if panel_name == "constraints":
            return [self.buffer.get("constraints", {})]
        if panel_name == "outputs":
            outputs = self.buffer.get("outputs", {})
            return list(outputs.get("required_artifacts", []))
        return list(self.buffer.get(panel_name, []))

    def _set_panel_rows(self, rows: list[Any], panel: str | None = None) -> None:
        panel_name = panel or self.session.selected_panel
        if panel_name == "constraints":
            self.buffer["constraints"] = rows[0] if rows else {}
            return
        if panel_name == "outputs":
            self.buffer.setdefault("outputs", {})
            self.buffer["outputs"]["required_artifacts"] = rows
            return
        self.buffer[panel_name] = rows

    def _panel_columns(self) -> list[str]:
        panel = self.session.selected_panel
        if panel == "steps":
            return ["id", "kind", "group", "depends_on", "agent_id", "task", "gate_mode", "gate_pass_if"]
        if panel == "edges":
            return ["from_node", "to", "when", "loop_id"]
        if panel == "loops":
            return ["id", "max_iterations"]
        if panel == "agents":
            return ["id", "provider", "model", "system_prompt", "tools_allow"]
        if panel == "constraints":
            return ["max_runtime_seconds", "max_total_steps", "max_cost_usd", "fail_fast"]
        return ["id", "format"]

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        if hasattr(row, "model_dump"):
            data = row.model_dump(mode="json")
        elif isinstance(row, dict):
            data = dict(row)
        else:
            data = {"value": str(row)}
        for key, value in list(data.items()):
            if isinstance(value, list):
                data[key] = ", ".join(str(item) for item in value)
        return data

    def _selected_row_index(self) -> int | None:
        table = self.query_one("#editor-rows", DataTable)
        return table.cursor_row

    def _selected_row(self) -> Any | None:
        idx = self._selected_row_index()
        rows = self._panel_rows()
        if idx is None or idx < 0 or idx >= len(rows):
            return None
        return rows[idx]

    def _render_rows(self) -> None:
        table = self.query_one("#editor-rows", DataTable)
        table.clear(columns=True)
        columns = self._panel_columns()
        table.add_columns(*columns)
        for row in self._panel_rows():
            row_dict = self._row_to_dict(row)
            table.add_row(*[str(row_dict.get(col, "")) for col in columns])
        if table.row_count:
            table.move_cursor(row=min(self.session.selected_index, table.row_count - 1), column=0)

    def _render_status(self) -> None:
        status = self.query_one("#editor-status", Static)
        dirty = "dirty" if self.session.dirty else "clean"
        status.update(
            f"Editor | panel={self.session.selected_panel} | {dirty} | plan={self.session.plan_path}"
        )

    def _render_form(self) -> None:
        widget = self.query_one("#editor-form", Static)
        row = self._selected_row()
        if row is None:
            widget.update("No row selected.")
            return
        row_dict = self._row_to_dict(row)
        details = "\n".join([f"{key}: {value}" for key, value in row_dict.items()])
        widget.update(f"Selected Row Fields\n{details}")

    def _render_diagnostics(self) -> None:
        widget = self.query_one("#editor-diagnostics", Static)
        if not self.issues:
            widget.update("No validation issues.")
            return
        lines = [f"{issue.get('code')}: {issue.get('message')} ({issue.get('path')})" for issue in self.issues]
        if self.fixes:
            lines.append("Suggested fixes:")
            lines.extend([f"- {fix.title}" for fix in self.fixes])
        widget.update("\n".join(lines))

    def _render_all(self) -> None:
        self._render_status()
        self._render_rows()
        self._render_form()
        self._render_diagnostics()

    def _mark_dirty(self) -> None:
        self.session.dirty = True
        self._render_status()

    def action_cursor_down(self) -> None:
        table = self.query_one("#editor-rows", DataTable)
        table.action_cursor_down()
        self.session.selected_index = table.cursor_row or 0
        self._render_form()

    def action_cursor_up(self) -> None:
        table = self.query_one("#editor-rows", DataTable)
        table.action_cursor_up()
        self.session.selected_index = table.cursor_row or 0
        self._render_form()

    def action_panel_next(self) -> None:
        current = self.PANEL_ORDER.index(self.session.selected_panel)
        self.session.selected_panel = self.PANEL_ORDER[(current + 1) % len(self.PANEL_ORDER)]
        self.session.selected_index = 0
        self._autosave_tick()
        self._render_all()

    def action_panel_prev(self) -> None:
        current = self.PANEL_ORDER.index(self.session.selected_panel)
        self.session.selected_panel = self.PANEL_ORDER[(current - 1) % len(self.PANEL_ORDER)]
        self.session.selected_index = 0
        self._autosave_tick()
        self._render_all()

    def action_add_row(self) -> None:
        panel = self.session.selected_panel
        rows = self._panel_rows(panel)
        if panel == "steps":
            rows.append(
                {
                    "id": f"n{len(rows) + 1}",
                    "kind": "agent",
                    "group": "execution",
                    "depends_on": [],
                    "agent_id": "worker",
                    "task": "Describe task",
                }
            )
        elif panel == "edges":
            rows.append({"from_node": "", "to": "", "when": "success", "loop_id": ""})
        elif panel == "loops":
            rows.append({"id": f"loop_{len(rows) + 1}", "max_iterations": 3})
        elif panel == "agents":
            rows.append(
                {
                    "id": f"agent_{len(rows) + 1}",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "system_prompt": "",
                    "tools_allow": ["tool:*", "mcp:*"],
                }
            )
        elif panel == "outputs":
            rows.append({"id": f"artifact_{len(rows) + 1}", "format": "markdown"})
        else:
            return

        self._set_panel_rows(rows, panel)
        self._mark_dirty()
        self._render_all()

    def action_delete_row(self) -> None:
        idx = self._selected_row_index()
        if idx is None:
            return
        rows = self._panel_rows()
        if idx < 0 or idx >= len(rows):
            return
        rows.pop(idx)
        self._set_panel_rows(rows)
        self.session.selected_index = max(0, idx - 1)
        self._mark_dirty()
        self._render_all()

    def _apply_field_update(self, field: str, value: str) -> None:
        idx = self._selected_row_index()
        if idx is None:
            return
        rows = self._panel_rows()
        if idx < 0 or idx >= len(rows):
            return
        row = rows[idx]

        def assign(target: Any, key: str, val: Any) -> None:
            if hasattr(target, key):
                setattr(target, key, val)
            elif isinstance(target, dict):
                target[key] = val

        casted: Any = value
        if field in {"depends_on", "tools_allow"}:
            casted = split_csv(value)
        elif field in {"max_runtime_seconds", "max_total_steps", "max_iterations"}:
            casted = int(value or "0")
        elif field == "fail_fast":
            casted = value.strip().lower() in {"1", "true", "yes", "y"}

        assign(row, field, casted)
        self._set_panel_rows(rows)

    def action_edit_field(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        row_dict = self._row_to_dict(row)
        if not row_dict:
            return
        first_field = list(row_dict.keys())[0]
        first_value = str(row_dict.get(first_field, ""))

        def _done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            field, value = result
            if not field:
                return
            self._apply_field_update(field, value)
            self._mark_dirty()
            self._render_all()

        self.app.push_screen(FieldEditModal(first_field, first_value), _done)

    def _plan_content(self) -> str:
        payload = rows_to_plan_data(self.buffer)
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)

    def action_validate(self) -> None:
        content = self._plan_content()
        payload = yaml.safe_load(content)
        valid, issues, _summary = validate_plan_content(content)
        self.issues = issues
        self.fixes = suggest_fixes(payload, issues) if isinstance(payload, dict) else []
        if valid:
            self.query_one("#editor-diagnostics", Static).update("Plan valid.")
        self._render_diagnostics()

    def action_apply_fix(self) -> None:
        if not self.fixes:
            return
        content = self._plan_content()
        payload = yaml.safe_load(content)
        if not isinstance(payload, dict):
            return
        fixed = apply_fix(payload, self.fixes[0])
        fixed_content = yaml.safe_dump(fixed, sort_keys=False, allow_unicode=False)
        plan = parse_plan_yaml(fixed_content)
        self.buffer = plan_to_rows(plan)
        self._mark_dirty()
        self.action_validate()
        self._render_all()

    def action_save(self) -> None:
        content = self._plan_content()
        valid, issues, _summary = validate_plan_content(content)
        self.issues = issues
        if not valid:
            self.fixes = suggest_fixes(yaml.safe_load(content) or {}, issues)
            self._render_diagnostics()
            return

        payload = yaml.safe_load(content)
        plan_id = str(payload.get("plan_id", "edited-plan")) if isinstance(payload, dict) else "edited-plan"
        filename = versioned_filename(plan_id, "edited")
        path = self.shell.orchestrator.paths["plans"] / filename
        path.write_text(content, encoding="utf-8")

        self.session.plan_path = str(path)
        self.session.dirty = False
        self.session.last_saved_at = datetime.now(timezone.utc).isoformat()
        self.fixes = []
        self.issues = []
        self._render_all()

    def action_save_draft_only(self) -> None:
        content = self._plan_content()
        draft = PlanDraftState(
            id=self.session.draft_id,
            path=str(self.shell.orchestrator.paths["drafts"] / f"{self.session.draft_id}.yaml"),
            title=self.session.draft_id,
            content=content,
            autosave=True,
            meta={"panel": self.session.selected_panel},
        )
        autosave_snapshot(self.shell.orchestrator.paths["drafts"], draft_id=draft.id, content=draft.content)

    def _autosave_tick(self) -> None:
        if not self.session.dirty:
            return
        self.action_save_draft_only()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("panel-"):
            return
        panel = button_id.split("panel-", 1)[1]
        if panel in self.PANEL_ORDER:
            self.session.selected_panel = panel
            self.session.selected_index = 0
            self._render_all()
