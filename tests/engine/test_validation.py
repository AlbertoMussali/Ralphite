from __future__ import annotations

from ralphite.engine.models import ValidationFix
from ralphite.engine.validation import apply_fix, suggest_fixes, validate_plan_content


def _minimal_v1_content(*, agents_block: str, tasks_block: str) -> str:
    return f"""
version: 1
plan_id: sample
name: sample
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
constraints:
  max_parallel: 2
agents:
{agents_block}
tasks:
{tasks_block}
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
outputs:
  required_artifacts: []
"""


def test_non_v1_plan_is_rejected_with_guidance() -> None:
    content = """
version: 3
plan_id: old
name: old
"""
    valid, issues, summary = validate_plan_content(content)
    assert not valid
    assert summary.get("expected_version") == 1
    assert any(issue.get("code") == "version.invalid" for issue in issues)

    fixes = suggest_fixes({}, issues)
    assert fixes == []
    noop = ValidationFix(code="noop", title="noop", description="noop", path="root")
    assert apply_fix({"version": 1}, fix=noop) == {"version": 1}


def test_validate_plan_v1_with_embedded_tasks() -> None:
    content = _minimal_v1_content(
        agents_block="""
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
""",
        tasks_block="""
  - id: t1
    title: Plan work
    completed: false
    routing:
      cell: seq_pre
      tags: [planning]
  - id: t2
    title: Execute work
    completed: false
    deps: [t1]
    routing:
      cell: par_core
      tags: [build]
  - id: t3
    title: Verify work
    completed: false
    deps: [t2]
    routing:
      cell: seq_post
      tags: [verify]
""",
    )
    valid, issues, summary = validate_plan_content(content)
    assert valid is True, issues
    assert summary.get("version") == 1
    assert summary.get("phases") == 1
    assert summary.get("template") == "general_sps"
    assert summary.get("tasks_status", {}).get("status") == "ok"
    assert summary.get("parallel_limit") == 2


def test_suggest_fixes_adds_missing_worker_agent() -> None:
    plan_data = {
        "version": 1,
        "plan_id": "missing_worker",
        "name": "missing_worker",
        "materials": {
            "autodiscover": {"enabled": False, "path": ".", "include_globs": []},
            "includes": [],
            "uploads": [],
        },
        "constraints": {"max_parallel": 1},
        "agents": [
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "codex",
                "model": "gpt-5.3-codex",
            }
        ],
        "tasks": [{"id": "t1", "title": "task", "completed": False}],
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "behaviors": [
                {
                    "id": "merge_default",
                    "kind": "merge_and_conflict_resolution",
                    "agent": "orchestrator_default",
                    "enabled": True,
                }
            ],
            "branched": {"lanes": ["lane_a", "lane_b"]},
            "blue_red": {"loop_unit": "per_task"},
            "custom": {"cells": []},
        },
        "outputs": {"required_artifacts": []},
    }
    content = _minimal_v1_content(
        agents_block="""
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
""",
        tasks_block="""
  - id: t1
    title: task
    completed: false
""",
    )
    valid, issues, _summary = validate_plan_content(content)
    assert valid is False
    fixes = suggest_fixes(plan_data, issues)
    assert any(fix.code == "fix.add_default_worker" for fix in fixes)

    add_worker = next(fix for fix in fixes if fix.code == "fix.add_default_worker")
    updated = apply_fix(plan_data, add_worker)
    assert any(agent.get("role") == "worker" for agent in updated.get("agents", []))


def test_suggest_fixes_removes_forward_dependency() -> None:
    plan_data = {
        "version": 1,
        "plan_id": "forward_dep",
        "name": "forward_dep",
        "materials": {
            "autodiscover": {"enabled": False, "path": ".", "include_globs": []},
            "includes": [],
            "uploads": [],
        },
        "constraints": {"max_parallel": 1},
        "agents": [
            {
                "id": "worker_default",
                "role": "worker",
                "provider": "codex",
                "model": "gpt-5.3-codex",
            },
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "codex",
                "model": "gpt-5.3-codex",
            },
        ],
        "tasks": [
            {"id": "t1", "title": "one", "completed": False, "deps": ["t2"]},
            {"id": "t2", "title": "two", "completed": False},
        ],
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "behaviors": [
                {
                    "id": "merge_default",
                    "kind": "merge_and_conflict_resolution",
                    "agent": "orchestrator_default",
                    "enabled": True,
                }
            ],
            "branched": {"lanes": ["lane_a", "lane_b"]},
            "blue_red": {"loop_unit": "per_task"},
            "custom": {"cells": []},
        },
        "outputs": {"required_artifacts": []},
    }
    content = _minimal_v1_content(
        agents_block="""
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
""",
        tasks_block="""
  - id: t1
    title: one
    completed: false
    deps: [t2]
  - id: t2
    title: two
    completed: false
""",
    )
    valid, issues, _summary = validate_plan_content(content)
    assert valid is False
    fixes = suggest_fixes(plan_data, issues)
    dep_fix = next(fix for fix in fixes if fix.code == "fix.clean_invalid_deps")
    updated = apply_fix(plan_data, dep_fix)
    assert updated["tasks"][0]["deps"] == []


def test_validation_issues_are_deduplicated_for_branched_unassigned_tasks() -> None:
    content = """
version: 1
plan_id: branched_dedupe
name: branched_dedupe
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
constraints:
  max_parallel: 2
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
tasks:
  - id: t1
    title: Lane task
    completed: false
orchestration:
  template: branched
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
outputs:
  required_artifacts: []
"""
    valid, issues, summary = validate_plan_content(content)
    assert valid is False
    keys = [
        (row.get("code"), row.get("path"), row.get("message"), row.get("level"))
        for row in issues
    ]
    assert len(keys) == len(set(keys))
    assert isinstance(summary.get("recommended_commands"), list)


def test_validation_rejects_out_of_bounds_artifact_glob() -> None:
    content = _minimal_v1_content(
        agents_block="""
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
""",
        tasks_block="""
  - id: t1
    title: Build
    completed: false
    acceptance:
      required_artifacts:
        - id: bundle
          path_glob: ../../*
          format: file
""",
    )
    valid, issues, _summary = validate_plan_content(content)
    assert valid is False
    assert any(
        issue.get("code") == "tasks.acceptance.path_glob_out_of_bounds"
        for issue in issues
    )


def test_validation_rejects_invalid_prompt_placeholder_for_role() -> None:
    content = _minimal_v1_content(
        agents_block="""
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    system_prompt: "Worker cannot use {{behavior_kind}}"
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
""",
        tasks_block="""
  - id: t1
    title: Build
    completed: false
""",
    )
    valid, issues, _summary = validate_plan_content(content)
    assert valid is False
    assert any(
        issue.get("code") == "defaults.placeholder_invalid" for issue in issues
    )
