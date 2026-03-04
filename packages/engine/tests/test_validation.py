from __future__ import annotations

from pathlib import Path

from ralphite_engine.models import ValidationFix
from ralphite_engine.validation import apply_fix, suggest_fixes, validate_plan_content


def test_v1_plan_is_rejected_with_migration_guidance() -> None:
    content = """
version: 1
plan_id: empty
name: empty
"""
    valid, issues, summary = validate_plan_content(content)
    assert not valid
    assert summary.get("supported_versions") == [2]
    assert any(issue.get("code") == "version.deprecated_v1" for issue in issues)

    fixes = suggest_fixes({}, issues)
    assert fixes == []
    noop = ValidationFix(code="noop", title="noop", description="noop", path="root")
    assert apply_fix({"version": 2}, fix=noop) == {"version": 2}


def test_validate_plan_v2_with_task_source(tmp_path: Path) -> None:
    task_file = tmp_path / "RALPHEX_TASK.md"
    task_file.write_text(
        "\n".join(
            [
                "# Tasks",
                "",
                "- [ ] Plan work <!-- id:t1 phase:phase-1 lane:seq_pre agent_profile:worker_default -->",
                "- [ ] Execute work <!-- id:t2 phase:phase-1 lane:parallel deps:t1 agent_profile:worker_default -->",
                "- [ ] Verify work <!-- id:t3 phase:phase-1 lane:seq_post deps:t2 agent_profile:worker_default -->",
                "",
            ]
        ),
        encoding="utf-8",
    )

    content = """
version: 2
plan_id: v2_sample
name: v2_sample
task_source:
  kind: markdown_checklist
  path: RALPHEX_TASK.md
  parser_version: 2
agent_profiles:
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
execution_structure:
  phases:
    - id: phase-1
      pre_orchestrator:
        enabled: false
        agent_profile_id: orchestrator_pre_default
      workers:
        sequential_before: []
        parallel: []
        sequential_after: []
      post_orchestrator:
        enabled: true
        agent_profile_id: orchestrator_post_default
constraints:
  max_parallel: 2
"""
    valid, issues, summary = validate_plan_content(content, workspace_root=tmp_path)
    assert valid is True, issues
    assert summary.get("version") == 2
    assert summary.get("phases") == 1
    assert summary.get("task_source_status", {}).get("status") == "ok"
