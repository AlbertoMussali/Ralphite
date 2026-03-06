# Recovery Workflow

Owners: engine, cli
Last verified against commit: 70b0c1f

## Git Prerequisites

Ralphite now distinguishes between two git readiness levels:

- Repository ready: inside a git worktree and has an initial commit.
- Execution ready: repository ready and the primary workspace is clean.

Command policy:

- `run` and `quickstart` require execution-ready state.
- `recover` and `replay` require repository-ready state.
- `run`, `quickstart`, and `replay` also block when recoverable runs or stale managed Ralphite worktrees/branches are still present in the workspace.
- A dirty workspace is allowed for `recover` and `replay`, but the CLI warns that local edits may still create merge conflicts.

## Operator Loop

```bash
uv run ralphite recover --workspace . --preflight-only --output json
```

Then:

1. Inspect `history` if you need the exact run id.
2. If a new `run`, `quickstart`, or `replay` was blocked by stale recovery state, resolve that run first instead of starting another one.
3. Run recovery preflight.
4. Use the recommended recovery mode, or override it explicitly if you have a better operator reason.
5. Resume the run.

```bash
uv run ralphite history --workspace . --output table
uv run ralphite recover --workspace . --run-id <RUN_ID> --preflight-only --output table
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode <MODE> --resume --output table
```

## Recommended Modes

- `manual`
  Use when conflict files, unresolved merge markers, or overlapping local base-workspace edits are present. This is the conservative default.
- `agent_best_effort`
  Use when the run is recoverable and the integration worktree is safe for agent remediation.
- `abort_phase`
  Use when the phase-level recovery state is not safely recoverable and continuing would be misleading or unsafe.

## Optional Inline Auto Recovery

`run` and `quickstart` accept:

```bash
uv run ralphite run --workspace . --first-failure-recovery agent_best_effort --output table
```

This performs one automatic `agent_best_effort` recovery attempt at the first recoverable integration failure. Ralphite still pauses instead of forcing through unsafe cases such as overlapping local edits in the primary workspace.

## Manual Recovery

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode manual --output json
```

Use this when the CLI recommends `manual`, especially for merge markers and explicit conflict files.

## Agent Best Effort

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode agent_best_effort --prompt "resolve conflicts" --resume --output json
```

Use this when the CLI recommends `agent_best_effort` and you can provide a concrete remediation prompt.

## Abort Phase

```bash
uv run ralphite recover --workspace . --run-id <RUN_ID> --mode abort_phase --resume --output json
```

Use this when the CLI recommends aborting the blocked phase instead of attempting further remediation.

## Exit Code Contract

- `0` success
- `10` no recoverable run
- `11` unrecoverable/not found
- `12` invalid input/mode
- `13` preflight failed
- `14` pending
- `15` terminal failure
- `16` internal error
