# Product Readiness Polish Scope

Owners: release, cli, engine, docs
Plan: `product_readiness_polish`
Task anchor: `task_define_polish_scope`
Status: Confirmed scope and verification bar

## Goal
Deliver one coordinated product-readiness polish pass that improves operator clarity and reliability across five surfaces before release:

1. Doc entrypoint consolidation.
2. Init/onboarding UX.
3. Recovery guidance quality.
4. Final report contract quality.
5. Cold-start + dirty-worktree behavior.

## In Scope

### 1) Docs Entrypoints
- Keep one canonical operator path and one canonical contributor path.
- Reduce overlap/conflicts between `README.md`, `USER_GUIDE.md`, and docs workflow/reference pages.
- Preserve alignment with current runtime and CLI contracts.

Primary surfaces:
- `README.md`
- `USER_GUIDE.md`
- `docs/index.md`
- `docs/workflows/first-run.md`
- `docs/workflows/index.md`
- `docs/references/cli-contracts.md`

### 2) Init + Onboarding Polish
- Make starter/template choice and defaults easier to understand in product terms.
- Ensure init/quickstart output explains what was created and what to do next.
- Keep compatibility with plan schema and runtime orchestration contracts.

Primary surfaces:
- `src/ralphite/cli/commands/init_cmd.py`
- `src/ralphite/cli/commands/quickstart_cmd.py`
- `src/ralphite/cli/core.py`
- `examples/plans/starter_*.yaml`
- `examples/agent_defaults.yaml`

### 3) Recovery Guidance Quality
- Move from mode listing to mode recommendation when runtime context is sufficient.
- Keep machine-readable outputs stable while improving operator-directed next actions.

Primary surfaces:
- `src/ralphite/cli/commands/recover_cmd.py`
- `src/ralphite/cli/core.py`
- `src/ralphite/engine/orchestrator.py`
- `src/ralphite/engine/presentation.py`
- `src/ralphite/engine/taxonomy.py`
- `docs/workflows/recovery.md`

### 4) Final Report Contract Quality
- Make final report minimum structure and coverage explicit.
- Keep report discoverability consistent in CLI outputs (stream/table/recovery-adjacent flows).
- Add/adjust tests to prevent regressions in artifact quality.

Primary surfaces:
- `src/ralphite/engine/reporting.py`
- `src/ralphite/engine/orchestrator.py`
- `src/ralphite/cli/core.py`
- `tests/engine/test_*report*`
- `tests/cli/test_cli_output_contract.py`

### 5) Cold-Start + Dirty Worktree Behavior
- Cover fresh non-git directories, git repos without initial commit, dirty worktrees, and healthy committed workspaces.
- Distinguish environment prerequisites from product failures in guidance and docs.

Primary surfaces:
- `src/ralphite/cli/doctoring.py`
- `src/ralphite/cli/commands/doctor_cmd.py`
- `src/ralphite/cli/commands/check_cmd.py`
- `src/ralphite/engine/git_worktree.py`
- `src/ralphite/engine/validation.py`
- `docs/workflows/release-readiness.md`
- `docs/decisions/ADR-0007-git-required-runtime.md`

## Explicit Non-Goals
- No new orchestration template family or schema version.
- No change to fail-closed git-required runtime policy.
- No expansion of scope beyond docs/onboarding/recovery/report/environment-readiness polish.
- No redesign of unrelated command families.

## Success Bar For Final Merge Task
Final merge (`task_merge_and_verify_readiness`) is ready only if:

1. Each of the five scope areas ships user-visible improvements with matching tests/docs.
2. Product-facing polish is clearly separated from deferred follow-up work.
3. Validation evidence is complete:
   - `uv run ruff check .`
   - `uv run --no-sync pytest -q`
   - `uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --full --output json`
   - `uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json`
4. Final report artifact is present and useful:
   - `.ralphite/artifacts/*/final_report.md`

## Execution Notes For Downstream Lanes
- Prefer tightening existing surfaces over adding new top-level docs/routes.
- Preserve output contract compatibility when improving human-facing wording.
- Keep strict-check policy and ADR requirements unchanged unless explicitly intentional.
