from __future__ import annotations

from ralphite_engine.validation import apply_fix, suggest_fixes, validate_plan_content


def test_suggest_and_apply_default_node_fix() -> None:
    plan = {
        "version": 1,
        "plan_id": "empty",
        "name": "empty",
        "agents": [{"id": "worker", "provider": "openai", "model": "gpt-4.1-mini"}],
        "graph": {"nodes": []},
    }
    content = """
version: 1
plan_id: empty
name: empty
agents:
  - id: worker
    provider: openai
    model: gpt-4.1-mini
graph:
  nodes: []
"""
    valid, issues, _summary = validate_plan_content(content)
    assert not valid

    fixes = suggest_fixes(plan, issues)
    assert fixes
    fixed = apply_fix(plan, fixes[0])
    assert fixed["graph"]["nodes"]
