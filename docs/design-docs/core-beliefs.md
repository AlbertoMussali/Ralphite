# Core Beliefs

Owners: engine, cli
Last verified against commit: 70b0c1f

1. Local-first execution: all runtime behavior should run via local CLIs and local workspace state.
2. Deterministic gates: release confidence comes from repeatable checks and explicit typed failures.
3. Plan-centric workflow: v1 plan YAML is the execution contract.
4. Fail-closed reliability: merge conflicts and invalid outputs should pause/fail with actionable recovery.
5. Human override always available: operator can inspect, recover, and resume safely.
