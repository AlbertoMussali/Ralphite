from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from ralphite.engine import LocalOrchestrator
from ralphite.engine.validation import validate_plan_content


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ralphite Test"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ralphite@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


FIXTURE_PLANS = Path(__file__).resolve().parent / "fixtures" / "plans"
VALID_FIXTURES = [
    "general_sps_minimal.yaml",
    "branched_two_lane.yaml",
    "blue_red_per_task.yaml",
    "custom_linear_cells.yaml",
]


@pytest.fixture(autouse=True)
def _git_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_runtime_dispatched_graph_matches_validate_resolved_graph(
    tmp_path: Path, fixture_name: str
) -> None:
    workspace = tmp_path
    plans_dir = workspace / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    source = FIXTURE_PLANS / fixture_name
    target = plans_dir / fixture_name
    shutil.copy2(source, target)

    content = target.read_text(encoding="utf-8")
    valid, issues, summary = validate_plan_content(
        content, workspace_root=workspace, plan_path=str(target)
    )
    assert valid is True, issues
    expected = summary.get("resolved_execution", {})
    assert isinstance(expected, dict)

    orch = LocalOrchestrator(workspace, bootstrap=False)
    run_id = orch.start_run(plan_ref=str(target))
    assert orch.wait_for_run(run_id, timeout=12.0) is True
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "succeeded"

    actual = run.metadata.get("resolved_execution", {})
    assert isinstance(actual, dict)

    expected_nodes = expected.get("resolved_nodes", [])
    actual_nodes = actual.get("resolved_nodes", [])
    assert isinstance(expected_nodes, list)
    assert isinstance(actual_nodes, list)

    expected_node_ids = {
        str(node.get("id")) for node in expected_nodes if isinstance(node, dict)
    }
    actual_node_ids = {
        str(node.get("id")) for node in actual_nodes if isinstance(node, dict)
    }
    assert expected_node_ids == actual_node_ids

    expected_cell_ids = {
        str(cell.get("id"))
        for cell in expected.get("resolved_cells", [])
        if isinstance(expected.get("resolved_cells"), list) and isinstance(cell, dict)
    }
    actual_cell_ids = {
        str(cell.get("id"))
        for cell in actual.get("resolved_cells", [])
        if isinstance(actual.get("resolved_cells"), list) and isinstance(cell, dict)
    }
    assert expected_cell_ids == actual_cell_ids
    assert actual.get("task_assignment") == expected.get("task_assignment")

    bundle_artifact = next(
        (item for item in run.artifacts if item.get("id") == "machine_bundle"), None
    )
    assert isinstance(bundle_artifact, dict)
    bundle_path = Path(str(bundle_artifact.get("path")))
    assert bundle_path.exists()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle.get("plan_path") == run.plan_path
    assert isinstance(bundle.get("metrics"), dict)
    nodes = bundle.get("nodes", {})
    assert isinstance(nodes, dict)
    assert set(nodes.keys()) == expected_node_ids
    assert all(
        isinstance(row, dict)
        and str(row.get("status")) in {"succeeded", "failed", "blocked"}
        for row in nodes.values()
    )
