from ralphite_api.services.plan_service import validate_and_compile


def test_validate_plan_success() -> None:
    content = """
version: 1
plan_id: sample
name: sample
agents:
  - id: agent_a
    provider: openai
    model: gpt-4.1-mini
graph:
  nodes:
    - id: n1
      kind: agent
      agent_id: agent_a
      task: do thing
      depends_on: []
"""
    valid, issues, summary, diagnostics = validate_and_compile(content)
    assert valid is True
    assert issues == []
    assert summary["nodes"] == 1
    assert diagnostics["single_node_only"] is True


def test_validate_plan_unknown_node_fails() -> None:
    content = """
version: 1
plan_id: sample
name: sample
agents:
  - id: agent_a
    provider: openai
    model: gpt-4.1-mini
graph:
  nodes:
    - id: n1
      kind: agent
      agent_id: agent_a
      task: do thing
      depends_on: []
  edges:
    - from: n2
      to: n1
      when: success
"""
    valid, issues, _, diagnostics = validate_and_compile(content)
    assert valid is False
    assert any(issue["code"] == "edge.unknown_from" for issue in issues)
    assert diagnostics["empty_plan"] is False
