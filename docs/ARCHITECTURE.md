# Ralphite Architecture

## Canonical Surfaces

- `apps/tui`: primary UX (run setup, timeline, recovery, summary)
- `apps/tui/cli.py`: automation wrapper around the same engine
- `packages/engine`: local orchestrator, validation, compiler, git/worktree lifecycle, recovery
- `packages/schemas`: shared v4 schema + validation rules

## Plan Contract

Ralphite is v4-only. The single YAML plan is the source of truth for:

- task list
- run structure (pre/post orchestrator)
- agent configuration
- constraints

No task sidecar file and no multi-version bridge.

## Runtime Model

Single-phase block scheduler compiled from ordered tasks:

1. optional `run.pre_orchestrator`
2. task blocks from list order
3. optional `run.post_orchestrator`

Block construction:

- sequential block: consecutive tasks with `parallel_group` absent or `0`
- parallel block: consecutive tasks sharing `parallel_group > 0`

Execution guarantees:

- later blocks wait for earlier blocks
- parallel block concurrency bounded by `constraints.max_parallel`
- `fail_fast=true` blocks downstream tasks after failure

## Validation Invariants

- plan version must be `4`
- at least one worker agent
- orchestrator agent references must exist when enabled
- `parallel_group` monotonic by first appearance
- `parallel_group` contiguous (no split/rejoin)
- `deps` must reference existing earlier tasks
- no dependency cycles

## Recovery and Git Safety

- worker tasks run in managed run-scoped worktrees
- post-orchestrator integrates phase branch back to base branch
- unresolved conflicts are fail-closed (`paused_recovery_required`)
- recovery modes: `manual`, `agent_best_effort`, `abort_phase`
- `recovery_preflight` checks mode/prompt/locks/worktree/conflict markers before resume
- cleanup operations are idempotent
- stale managed artifacts reported by doctor

## Persistence

Per-run state lives in `.ralphite/runs/<run_id>/`:

- `run_state.json`
- `checkpoint.json`
- `event_log.ndjson`
- `lock`

Artifacts live in `.ralphite/artifacts/<run_id>/`.

## TUI-First Boundary

Run Setup edits run controls plus task-level fields (`title`, `deps`, `agent`, `parallel_group`, `completed`) from the same YAML plan, with per-row validation badges and safe-fix diff preview (`Apply` -> `Accept/Reject`). Task order semantics remain task-list driven and are not edited as separate lane selectors.

## Operator Playbook

User-facing operational workflows are documented in `docs/USER_CENTERED_PLAYBOOK.md`.
