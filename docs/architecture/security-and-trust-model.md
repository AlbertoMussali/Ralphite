# Security and Trust Model

Owners: engine, release
Last verified against commit: 071697a

## Execution Trust Boundaries

- Ralphite invokes local headless CLIs with explicit flags.
- Worktree path is explicit in both process `cwd` and node prompts.
- Backend text that mentions paths outside the worktree is treated as diagnostics, not authority.
- Git/worktree inspection remains the source of truth for retained work and reconciliation.
- For JSON-emitting backends such as Cursor, the guard evaluates the final agent message payload rather than the raw transport envelope.

## Policy Controls

- Task `write_policy` is enforced locally from observed worktree mutations.
- Tool/MCP allow/deny lists in local config.
- Doctor and gate checks identify missing commands, model probe issues, and plan validity.
- Strict check mode requires doctor success plus backend smoke and validation suites.

## Governance

- ADR required for architecture/runtime contract changes.
- Docs and tests must be updated in the same PR for contract changes.
