from __future__ import annotations

from ralphite_engine.models import ValidationFix
from ralphite_engine.validation import apply_fix, suggest_fixes, validate_plan_content


def test_non_v4_plan_is_rejected_with_guidance() -> None:
    content = """
version: 3
plan_id: old
name: old
"""
    valid, issues, summary = validate_plan_content(content)
    assert not valid
    assert summary.get("supported_versions") == [4]
    assert any(issue.get("code") == "version.unsupported" for issue in issues)

    fixes = suggest_fixes({}, issues)
    assert fixes == []
    noop = ValidationFix(code="noop", title="noop", description="noop", path="root")
    assert apply_fix({"version": 4}, fix=noop) == {"version": 4}


def test_validate_plan_v4_with_embedded_tasks() -> None:
    content = """
version: 4
plan_id: v4_sample
name: v4_sample
run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default
constraints:
  max_parallel: 2
agents:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1-mini
    tools_allow: [tool:*]
  - id: orchestrator_pre_default
    role: orchestrator_pre
    provider: openai
    model: gpt-4.1-mini
  - id: orchestrator_post_default
    role: orchestrator_post
    provider: openai
    model: gpt-4.1-mini
tasks:
  - id: t1
    title: Plan work
    completed: false
  - id: t2
    title: Execute work
    completed: false
    parallel_group: 1
    deps: [t1]
  - id: t3
    title: Verify work
    completed: false
    deps: [t2]
"""
    valid, issues, summary = validate_plan_content(content)
    assert valid is True, issues
    assert summary.get("version") == 4
    assert summary.get("phases") == 1
    assert summary.get("tasks_status", {}).get("status") == "ok"
    assert summary.get("parallel_limit") == 2


def test_suggest_fixes_adds_missing_worker_agent() -> None:
    plan_data = {
        "version": 4,
        "plan_id": "missing_worker",
        "name": "missing_worker",
        "run": {
            "pre_orchestrator": {"enabled": False, "agent": "orchestrator_pre_default"},
            "post_orchestrator": {"enabled": True, "agent": "orchestrator_post_default"},
        },
        "constraints": {"max_parallel": 1},
        "agents": [
            {"id": "orchestrator_pre_default", "role": "orchestrator_pre", "provider": "openai", "model": "gpt-4.1-mini"},
            {"id": "orchestrator_post_default", "role": "orchestrator_post", "provider": "openai", "model": "gpt-4.1-mini"},
        ],
        "tasks": [{"id": "t1", "title": "task", "completed": False}],
    }
    content = """
version: 4
plan_id: missing_worker
name: missing_worker
run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default
constraints:
  max_parallel: 1
agents:
  - id: orchestrator_pre_default
    role: orchestrator_pre
    provider: openai
    model: gpt-4.1-mini
  - id: orchestrator_post_default
    role: orchestrator_post
    provider: openai
    model: gpt-4.1-mini
tasks:
  - id: t1
    title: task
    completed: false
"""
    valid, issues, _summary = validate_plan_content(content)
    assert valid is False
    fixes = suggest_fixes(plan_data, issues)
    assert any(fix.code == "fix.add_default_worker" for fix in fixes)

    add_worker = next(fix for fix in fixes if fix.code == "fix.add_default_worker")
    updated = apply_fix(plan_data, add_worker)
    assert any(agent.get("role") == "worker" for agent in updated.get("agents", []))


def test_suggest_fixes_removes_forward_dependency() -> None:
    plan_data = {
        "version": 4,
        "plan_id": "forward_dep",
        "name": "forward_dep",
        "run": {
            "pre_orchestrator": {"enabled": False, "agent": "orchestrator_pre_default"},
            "post_orchestrator": {"enabled": True, "agent": "orchestrator_post_default"},
        },
        "constraints": {"max_parallel": 1},
        "agents": [
            {"id": "worker_default", "role": "worker", "provider": "openai", "model": "gpt-4.1-mini"},
            {"id": "orchestrator_pre_default", "role": "orchestrator_pre", "provider": "openai", "model": "gpt-4.1-mini"},
            {"id": "orchestrator_post_default", "role": "orchestrator_post", "provider": "openai", "model": "gpt-4.1-mini"},
        ],
        "tasks": [
            {"id": "t1", "title": "one", "completed": False, "deps": ["t2"]},
            {"id": "t2", "title": "two", "completed": False},
        ],
    }
    content = """
version: 4
plan_id: forward_dep
name: forward_dep
run:
  pre_orchestrator:
    enabled: false
    agent: orchestrator_pre_default
  post_orchestrator:
    enabled: true
    agent: orchestrator_post_default
constraints:
  max_parallel: 1
agents:
  - id: worker_default
    role: worker
    provider: openai
    model: gpt-4.1-mini
  - id: orchestrator_pre_default
    role: orchestrator_pre
    provider: openai
    model: gpt-4.1-mini
  - id: orchestrator_post_default
    role: orchestrator_post
    provider: openai
    model: gpt-4.1-mini
tasks:
  - id: t1
    title: one
    completed: false
    deps: [t2]
  - id: t2
    title: two
    completed: false
"""
    valid, issues, _summary = validate_plan_content(content)
    assert valid is False
    fixes = suggest_fixes(plan_data, issues)
    dep_fix = next(fix for fix in fixes if fix.code == "fix.clean_invalid_deps")
    updated = apply_fix(plan_data, dep_fix)
    assert updated["tasks"][0]["deps"] == []
