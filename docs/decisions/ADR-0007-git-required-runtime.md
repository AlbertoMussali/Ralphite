# ADR-0007: Git-Required Runtime and Human Summary Artifact

- Status: Accepted
- Date: 2026-03-05
- Owners: engine, cli, release

## Context

Ralphite previously carried non-git execution fallbacks even though its runtime model depends on git worktrees, branch-based integration, recovery, and exact changed-file reporting. Those fallbacks made the product feel inconsistent: artifact quality degraded outside git, recovery semantics diverged, and the primary run summary could not reliably tell a human what changed.

## Decision

- Ralphite execution and recovery require the workspace to be inside a git worktree.
- `doctor` reports git readiness through a blocking `git-worktree` check.
- `run`, `quickstart`, `recover`, and `replay` fail closed outside git with typed guidance.
- Non-git execution fallbacks are removed from runtime orchestration.
- `final_report.md` remains the existing human-facing artifact id/path and is upgraded into the canonical run brief:
  - outcome
  - changed files
  - acceptance results
  - failures and warnings
  - next steps
  - supporting artifacts
  - run highlights

## Alternatives Considered

1. Keep non-git execution as best effort.
2. Add a second human-summary artifact while leaving `final_report` as a raw event dump.
3. Keep git optional and show best-effort file lists.

## Consequences

- Recovery, merge behavior, and changed-file reporting are now consistent across supported runs.
- Operator guidance is clearer because git readiness is checked before execution.
- Users must initialize git before first execution in a new workspace.
- Documentation and tests must treat git as a hard runtime prerequisite.

## Rollback / Migration Plan

If git-less execution needs to return, reintroduce it as an explicit, separately documented mode with its own artifact and recovery guarantees instead of a silent fallback.

## References

- `src/ralphite/engine/git_worktree.py`
- `src/ralphite/engine/orchestrator.py`
- `src/ralphite/engine/reporting.py`
- `src/ralphite/cli/doctoring.py`
