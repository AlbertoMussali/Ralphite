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
    provider: openai
    model: gpt-4.1-mini
  - id: orchestrator_default
    role: orchestrator
    provider: openai
    model: gpt-4.1-mini
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
