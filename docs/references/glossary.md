# Glossary

Owners: engine, tui
Last verified against commit: 70b0c1f

- **Plan v5**: single YAML execution contract for tasks + orchestration + agents.
- **Runtime node**: compiled execution unit (worker or orchestrator) in the DAG.
- **Behavior**: orchestrator operation binding (dispatch/merge/summarize/custom).
- **Acceptance**: task-level commands and required artifacts validated post-execution.
- **Beta gate**: strict `check` mode requiring doctor + backend smoke + release suites.
- **Worktree**: isolated git workspace path used by runtime execution.
