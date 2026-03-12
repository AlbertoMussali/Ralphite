from __future__ import annotations

import pytest


# Minimal valid agent_defaults.yaml satisfying AgentDefaultsSpec + contract validators
# (requires 1 worker, 1 orchestrator, and all 3 baseline behavior kinds enabled).
AGENT_DEFAULTS_YAML = """\
version: 1
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
behaviors:
  - id: prepare_dispatch_default
    kind: prepare_dispatch
    agent: orchestrator_default
    enabled: true
  - id: merge_default
    kind: merge_and_conflict_resolution
    agent: orchestrator_default
    enabled: true
  - id: summarize_default
    kind: summarize_work
    agent: orchestrator_default
    enabled: true
"""


@pytest.fixture(autouse=True)
def _enable_simulated_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPHITE_DEV_SIMULATED_EXECUTION", "1")

    from ralphite.engine import LocalOrchestrator

    monkeypatch.setattr(LocalOrchestrator, "require_git_workspace", lambda self: None)
