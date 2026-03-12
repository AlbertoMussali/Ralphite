# ADR-0008: Git Authority and Derived Recovery

Last verified against commit: 071697a

Status: Accepted

## Context

Ralphite orchestrates isolated worker worktrees and persists run/checkpoint state. Reliability issues appeared when cached run state or backend-reported claims disagreed with the repository state that actually existed on disk.

## Decision

- Git/worktree evidence is authoritative for mutation truth.
- Backend completion payloads are advisory and may contribute diagnostics, but they do not override observed local filesystem state.
- Recovery and resume flows must reconcile cached state from git truth before executing further work.
- `reconcile --apply` is the supported operator path for persisting repaired cached state.
- Salvage is a first-class workflow for malformed, partial, dirty, and committed worker outputs.
- Task write scope is enforced locally through plan-level write policy rather than prompt wording alone.

## Consequences

- Out-of-scope worker rejection is based on observed mutations, not text mentions of external paths.
- Run metadata is explicitly derived and may be rewritten during reconcile/recover/promote operations.
- Plan schema now carries write policy so high-risk tasks can be constrained to specific roots.
- Windows and cross-shell behavior must be handled as runtime contract concerns because path and cleanup semantics affect authority and recovery correctness.
