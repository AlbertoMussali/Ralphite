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
