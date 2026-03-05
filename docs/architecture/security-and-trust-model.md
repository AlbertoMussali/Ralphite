# Security and Trust Model

Owners: engine, release
Last verified against commit: 70b0c1f

## Execution Trust Boundaries

- Ralphite invokes local headless CLIs with explicit flags.
- Worktree path is explicit in both process `cwd` and node prompts.
- Output that claims changes outside the worktree is rejected.

## Policy Controls

- Tool/MCP allow/deny lists in local config.
- Doctor and gate checks identify missing commands, model probe issues, and plan validity.
- Strict check mode requires doctor success plus backend smoke and validation suites.

## Governance

- ADR required for architecture/runtime contract changes.
- Docs and tests must be updated in the same PR for contract changes.
