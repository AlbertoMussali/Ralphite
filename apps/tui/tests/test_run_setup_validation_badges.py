from __future__ import annotations

from types import MethodType

from ralphite_engine.task_parser import parse_plan_tasks
from ralphite_engine.validation import parse_plan_yaml
from ralphite_tui.tui.screens.run_setup_screen import RunSetupScreen


PLAN = """
version: 5
plan_id: setup
name: setup
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
tasks:
  - id: t1
    title: Task 1
    completed: false
  - id: t2
    title: Task 2
    completed: false
outputs:
  required_artifacts: []
"""


class _StaticSink:
    def __init__(self) -> None:
        self.text = ""

    def update(self, text: str) -> None:
        self.text = text


class _InputStub:
    def __init__(self, value: str) -> None:
        self.value = value


class _TableSink:
    def __init__(self) -> None:
        self.rows: list[tuple[str, ...]] = []

    def clear(self) -> None:
        self.rows.clear()

    def add_row(self, *values: str) -> None:
        self.rows.append(tuple(values))

    def move_cursor(self, row: int = 0, column: int = 0) -> None:  # noqa: ARG002
        return


def _build_screen() -> RunSetupScreen:
    model = parse_plan_yaml(PLAN)
    tasks, _issues = parse_plan_tasks(model)
    screen = RunSetupScreen()
    screen._tasks = tasks  # noqa: SLF001
    return screen


def test_task_badges_mark_specific_fields_from_issue_paths() -> None:
    screen = _build_screen()
    screen._latest_validation_issues = [  # noqa: SLF001
        {"code": "task.title.empty", "path": "tasks[0].title"},
        {"code": "task.deps.forward_reference", "path": "tasks[1].deps[0]"},
    ]
    screen._rebuild_task_badges()  # noqa: SLF001
    assert screen._task_badges[0]["title"] == "ERR(task.title.empty)"  # noqa: SLF001
    assert screen._task_badges[0]["deps"] == "OK"  # noqa: SLF001
    assert screen._task_badges[1]["deps"] == "ERR(task.deps.forward_reference)"  # noqa: SLF001
    assert screen._task_badges[1]["routing"] == "OK"  # noqa: SLF001


def test_task_badges_apply_global_routing_issue_to_all_rows() -> None:
    screen = _build_screen()
    screen._latest_validation_issues = [  # noqa: SLF001
        {"code": "tasks.unassigned", "path": "tasks"},
    ]
    screen._rebuild_task_badges()  # noqa: SLF001
    assert screen._task_badges[0]["routing"] == "ERR(tasks.unassigned)"  # noqa: SLF001
    assert screen._task_badges[1]["routing"] == "ERR(tasks.unassigned)"  # noqa: SLF001


def test_accept_and_reject_pending_fixes_updates_state() -> None:
    screen = _build_screen()
    sink = _StaticSink()
    status = _StaticSink()
    screen._fix_preview = MethodType(lambda self: sink, screen)  # type: ignore[method-assign]
    screen._status = MethodType(lambda self: status, screen)  # type: ignore[method-assign]
    screen._render_editor_tables = MethodType(lambda self: None, screen)  # type: ignore[method-assign]
    screen._refresh_validation = MethodType(lambda self: None, screen)  # type: ignore[method-assign]

    screen._loaded_plan_data = {"plan_id": "before"}  # noqa: SLF001
    screen._pending_fixed_plan_data = {"plan_id": "after"}  # noqa: SLF001
    screen._pending_fix_count = 2  # noqa: SLF001
    screen._accept_pending_fixes()  # noqa: SLF001
    assert screen._loaded_plan_data == {"plan_id": "after"}  # noqa: SLF001
    assert screen._pending_fixed_plan_data is None  # noqa: SLF001
    assert screen._pending_fix_count == 0  # noqa: SLF001
    assert sink.text == "Safe-fix preview accepted."
    assert "Accepted 2 safe fix(es)" in status.text

    screen._pending_fixed_plan_data = {"plan_id": "later"}  # noqa: SLF001
    screen._pending_fix_count = 1  # noqa: SLF001
    screen._pending_fix_diff = "--- diff ---"  # noqa: SLF001
    screen._reject_pending_fixes()  # noqa: SLF001
    assert screen._pending_fixed_plan_data is None  # noqa: SLF001
    assert screen._pending_fix_count == 0  # noqa: SLF001
    assert screen._pending_fix_diff == ""  # noqa: SLF001
    assert sink.text == "No safe-fix preview yet."
    assert "preview rejected" in status.text


def test_render_resolved_preview_uses_structured_row_format() -> None:
    screen = _build_screen()
    sink = _StaticSink()
    screen._structure = MethodType(lambda self: sink, screen)  # type: ignore[method-assign]
    screen._latest_validation_issues = [  # noqa: SLF001
        {
            "code": "tasks.unassigned",
            "message": "routing missing",
            "path": "tasks[0].routing",
        }
    ]
    summary = {
        "template": "general_sps",
        "resolved_execution": {
            "template": "general_sps",
            "resolved_cells": [{"id": "seq_pre", "kind": "sequential"}],
            "resolved_nodes": [
                {
                    "cell_id": "seq_pre",
                    "lane": "sequential",
                    "role": "worker",
                    "source_task_id": "t1",
                },
                {
                    "cell_id": "orch_merge_1",
                    "lane": "orchestrator",
                    "role": "orchestrator",
                    "source_task_id": None,
                },
            ],
            "compile_warnings": [],
        },
    }
    screen._render_resolved_preview(summary)  # noqa: SLF001
    assert "order | cell | lane | role | task_id" in sink.text
    assert "  1 | seq_pre | sequential | worker | t1" in sink.text
    assert "Unmapped-task warnings:" in sink.text


def test_apply_orchestration_edit_updates_template_and_config() -> None:
    screen = _build_screen()
    status = _StaticSink()
    screen._status = MethodType(lambda self: status, screen)  # type: ignore[method-assign]
    screen._clear_fix_preview = MethodType(lambda self: None, screen)  # type: ignore[method-assign]
    screen._render_editor_tables = MethodType(lambda self: None, screen)  # type: ignore[method-assign]
    screen._refresh_validation = MethodType(lambda self: None, screen)  # type: ignore[method-assign]
    screen._loaded_plan_data = {
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "branched": {"lanes": ["lane_a", "lane_b"]},
            "blue_red": {"loop_unit": "per_task"},
            "custom": {"cells": []},
        }
    }  # noqa: SLF001

    lookup = {
        "#edit-template": _InputStub("branched"),
        "#edit-branched-lanes": _InputStub("alpha,beta"),
        "#edit-loop-unit": _InputStub("per_task"),
    }

    def query_one_stub(self, selector, _type=None):  # noqa: ANN001
        if selector in lookup:
            return lookup[selector]
        raise AssertionError(f"unexpected selector {selector}")

    screen.query_one = MethodType(query_one_stub, screen)  # type: ignore[method-assign]
    screen._apply_orchestration_edit()  # noqa: SLF001
    orchestration = screen._loaded_plan_data["orchestration"]  # noqa: SLF001
    assert orchestration["template"] == "branched"
    assert orchestration["branched"]["lanes"] == ["alpha", "beta"]
    assert "Template/config set to branched" in status.text


def test_render_editor_tables_populates_title_column() -> None:
    screen = _build_screen()
    run_sink = _TableSink()
    task_sink = _TableSink()
    screen._run_table = MethodType(lambda self: run_sink, screen)  # type: ignore[method-assign]
    screen._tasks_table = MethodType(lambda self: task_sink, screen)  # type: ignore[method-assign]
    screen._populate_task_editor = MethodType(lambda self: None, screen)  # type: ignore[method-assign]
    screen._populate_orchestration_editor = MethodType(lambda self: None, screen)  # type: ignore[method-assign]
    screen._loaded_plan_data = {
        "constraints": {"max_parallel": 2},
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "behaviors": [],
            "branched": {"lanes": ["lane_a"]},
        },
        "version": 5,
    }  # noqa: SLF001
    screen._rebuild_task_badges()  # noqa: SLF001
    screen._render_editor_tables()  # noqa: SLF001
    assert task_sink.rows
    first_row = task_sink.rows[0]
    assert first_row[1] == "Task 1"
