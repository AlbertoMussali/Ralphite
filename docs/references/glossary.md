# Glossary

Owners: engine, cli
Last verified against commit: 70b0c1f

- **Plan v1**: single YAML execution contract for tasks + orchestration, with inline agents or `agent_defaults_ref`-based defaults.
- **Runtime node**: compiled execution unit (worker or orchestrator) in the DAG.
- **Behavior**: orchestrator operation binding (dispatch/merge/summarize/custom).
- **Acceptance**: task-level commands and required artifacts validated post-execution.
- **Strict check mode**: `check --strict`, requiring doctor + backend smoke + validation suites.
- **Worktree**: isolated git workspace path used by runtime execution.
