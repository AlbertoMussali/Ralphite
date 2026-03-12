# Security

Owners: engine, release
Last verified against commit: 70b0c1f

## Security Posture (Current)

- local CLI invocation with explicit flags
- worktree-scoped runtime execution
- configurable tool/MCP policy controls
- typed rejection of out-of-worktree change claims

## Security Maintenance

- document policy-affecting runtime changes in ADRs
- keep command contracts and docs synchronized with tests
