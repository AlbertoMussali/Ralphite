from __future__ import annotations

import re
from types import MethodType

from ralphite_tui.tui.screens.run_setup_screen import RunSetupScreen


class _StaticSink:
    def __init__(self) -> None:
        self.text = ""

    def update(self, text: str) -> None:
        self.text = text


def _screen_with_sink() -> tuple[RunSetupScreen, _StaticSink]:
    screen = RunSetupScreen()
    sink = _StaticSink()
    screen._structure = MethodType(lambda self: sink, screen)  # type: ignore[method-assign]
    return screen, sink


def test_preview_rows_are_parseable_with_expected_columns() -> None:
    screen, sink = _screen_with_sink()
    summary = {
        "template": "general_sps",
        "resolved_execution": {
            "template": "general_sps",
            "resolved_cells": [{"id": "seq_pre", "kind": "sequential"}],
            "resolved_nodes": [
                {
                    "id": "n1",
                    "cell_id": "seq_pre",
                    "lane": "sequential",
                    "role": "worker",
                    "source_task_id": "t1",
                },
                {
                    "id": "n2",
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
    rows = [
        line for line in sink.text.splitlines() if "|" in line and "order" not in line
    ]
    assert any(
        re.match(r"^\s*\d+\s+\|\s+[^|]+\|\s+[^|]+\|\s+[^|]+\|\s+.*$", row)
        for row in rows
    )


def test_preview_compact_truncates_and_verbose_expands() -> None:
    screen, sink = _screen_with_sink()
    nodes = [
        {
            "id": f"n{i}",
            "cell_id": f"cell_{i}",
            "lane": "sequential",
            "role": "worker",
            "source_task_id": f"t{i}",
        }
        for i in range(30)
    ]
    summary = {
        "template": "general_sps",
        "resolved_execution": {
            "template": "general_sps",
            "resolved_cells": [
                {"id": f"cell_{i}", "kind": "sequential"} for i in range(30)
            ],
            "resolved_nodes": nodes,
            "compile_warnings": [],
        },
    }
    screen._preview_verbose = False  # noqa: SLF001
    screen._render_resolved_preview(summary)  # noqa: SLF001
    assert "... (" in sink.text

    screen._preview_verbose = True  # noqa: SLF001
    screen._render_resolved_preview(summary)  # noqa: SLF001
    assert "... (" not in sink.text
    assert "cell_29" in sink.text


def test_preview_includes_unmapped_task_warning_section() -> None:
    screen, sink = _screen_with_sink()
    screen._latest_validation_issues = [  # noqa: SLF001
        {
            "code": "tasks.routing.missing",
            "message": "routing missing",
            "path": "tasks[0].routing",
        }
    ]
    summary = {
        "template": "branched",
        "resolved_execution": {
            "template": "branched",
            "resolved_cells": [{"id": "split_dispatch", "kind": "orchestrator"}],
            "resolved_nodes": [
                {
                    "id": "n1",
                    "cell_id": "split_dispatch",
                    "lane": "orchestrator",
                    "role": "orchestrator",
                }
            ],
            "compile_warnings": [],
        },
    }
    screen._render_resolved_preview(summary)  # noqa: SLF001
    assert "Unmapped-task warnings:" in sink.text
    assert "tasks.routing.missing" in sink.text
