# Runtime Execution

Owners: engine, cli
Last verified against commit: a8f4411

Source files:

- `src/ralphite/engine/orchestrator.py`
- `src/ralphite/engine/git_worktree.py`
- `src/ralphite/engine/git_runtime_repo.py`
- `src/ralphite/engine/git_runtime_paths.py`
- `src/ralphite/engine/git_runtime_conflicts.py`
- `src/ralphite/engine/git_runtime_state.py`
- `src/ralphite/engine/git_runtime_prepare.py`
- `src/ralphite/engine/git_runtime_cleanup.py`
- `src/ralphite/engine/headless_agent.py`
- `src/ralphite/engine/runtime_bootstrap.py`
- `src/ralphite/engine/runtime_node_runner.py`
- `src/ralphite/engine/runtime_recovery_manager.py`
- `src/ralphite/engine/runtime_execution_engine.py`
- `src/ralphite/engine/runtime_artifacts.py`
- `src/ralphite/engine/runtime_events.py`
- `src/ralphite/cli/commands/run_cmd.py`
- `src/ralphite/cli/commands/recover_cmd.py`

Related ADR:

- [ADR-0008: Git Authority and Derived Recovery](../decisions/ADR-0008-git-authority-and-derived-recovery.md)

## Core Model

Ralphite executes `version: 1` plans by combining:

- plan validation and runtime compilation
- isolated worker worktrees and phase integration branches
- typed local run/checkpoint state under `.ralphite/runs/`
- terminal artifacts under `.ralphite/artifacts/`
- explicit recovery, reconcile, and salvage workflows

The central runtime rule is:

- Git/worktree truth is authoritative for mutation and merge state.
- Backend payloads and summaries are advisory diagnostics unless local repository state confirms them.

`LocalOrchestrator` remains the stable engine entrypoint, but it now delegates runtime work to focused internal modules for bootstrap, node execution, recovery, artifacts, and event emission.

## Run Lifecycle

1. `run` or `quickstart` resolves a plan, validates it, materializes runtime nodes, and snapshots execution defaults.
2. Ralphite requires an execution-ready workspace for new runs:
   - inside a git worktree
   - initial commit exists
   - primary workspace is clean enough to launch a new run
3. Worker nodes prepare a phase branch and a per-worker worktree under `.ralphite/worktrees/`.
4. A worker backend runs inside the assigned worktree with an explicit prompt, explicit worktree path, and machine-enforced write policy.
5. Ralphite inspects local worktree state, classifies observed writes, commits worker output when valid, and runs task acceptance against the worker worktree.
6. Orchestrator merge nodes prepare a phase integration worktree, merge worker commits into the phase branch, then integrate the phase result back to the base branch.
7. Narrow deterministic conflict resolvers may auto-resolve additive export barrels and simple markdown append-only conflicts before recovery is required.
8. On terminal success, Ralphite writes terminal artifacts and cleans managed git artifacts when cleanup is safe.
9. On terminal non-success, Ralphite preserves managed worktrees and branches by default so the run can be reconciled or salvaged.

## Worker Execution Contract

Worker execution is bounded by all of the following:

- backend process `cwd` is the assigned worktree
- prompts include the assigned worktree path and write-policy summary
- worker completion payloads are captured as diagnostics
- local worktree inspection determines the actual changed-file truth

Backend result handling:

- `backend_out_of_worktree_claim` is diagnostic only when the backend mentions an external path but local state does not confirm a mutation.
- `backend_out_of_worktree_mutation` is fatal when observed local writes exceed the assigned write scope.
- `backend_payload_missing` means the backend exited cleanly without a final completion payload.
- `backend_payload_malformed` means the backend emitted a payload Ralphite could not reliably parse.

When backend payload handling fails, Ralphite still inspects the assigned worktree and retains salvageable work instead of discarding it.

For non-merge orchestrator nodes such as summary/handoff behaviors, Ralphite can also salvage real local workspace output after backend payload failure, but only when the pre-existing workspace dirtiness is limited to Ralphite bookkeeping surfaces.

## Write Scope Enforcement

Tasks may declare `write_policy`:

- `allowed_write_roots`
- `forbidden_write_roots`
- `allow_plan_edits`
- `allow_root_writes`

Runtime behavior:

- if `allowed_write_roots` is omitted, Ralphite derives a conservative allowlist from declared acceptance artifact roots when possible
- plan edits are forbidden unless `allow_plan_edits: true`
- forbidden roots win over allowed roots
- observed writes are classified from local git/worktree evidence, not backend text

This enforcement happens before worker output is accepted as valid runtime work.

## Acceptance Execution

Acceptance runs against the worker worktree after worker commit creation.

Acceptance contract:

- commands are executed as direct argv invocations, not shell-expanded command strings
- artifact globs must remain worktree-relative
- artifact matches are canonicalized back to the worktree boundary
- out-of-bounds artifact resolution is fatal

This means shell wildcard behavior is not part of the runtime contract. Plans should use explicit commands and worktree-relative artifact globs.

## Recovery, Reconcile, and Salvage

Recovery is not a separate product surface from execution. It is part of the runtime contract.

- `recover` resumes paused or recoverable runs with explicit recovery modes
- `reconcile` rebuilds run truth from persisted state plus live git/worktree state
- `reconcile --apply` persists repaired cached state back into run/checkpoint storage
- `salvage` inventories preserved work and orphaned managed artifacts
- `promote-salvage` promotes retained worker output back into the standard acceptance and integration path

`recover_run` and resume flows reconcile state before continuing execution.

Derived-state policy:

- merged work in git can repair stale failed/blocked node state
- stale prepared worktrees can be adopted or retried instead of treated as authoritative failure
- phase completeness is derived from node truth, not trusted from stale markers alone

## Retained Work and Salvage Classes

Non-success runs preserve managed work by default.

Retained work records include:

- scope, phase, node id
- branch and worktree path
- worktree/branch existence
- commit SHA when present
- changed-file and status evidence
- backend stdout/stderr excerpts and diagnostics
- salvage class

Current salvage classes include:

- `dirty_uncommitted`
- `committed_unmerged`
- `orphan_managed_artifact`

Dirty retained work can still be promoted if local acceptance passes and Ralphite can create the salvage promotion commit.

## Windows and Cross-Platform Runtime Notes

Windows is a supported local runtime target.

Current runtime hardening includes:
- direct argv acceptance execution with worktree-relative glob expansion before subprocess launch
- managed worktree cleanup retry/backoff around transient lock failures
- explicit long-path-risk diagnostics when managed worktree removal fails on Windows-like path limits

- automatic wrapping of PowerShell-backed backend launchers
- short hashed temp/cache/environment roots for worker subprocesses
- compact managed branch/worktree naming
- direct argv acceptance execution rather than shell-dependent expansion
- ASCII-safe fallback for stream-oriented CLI output when Rich rendering hits console encoding issues

Operator-facing docs should not assume Bash-only behavior for runtime correctness.
