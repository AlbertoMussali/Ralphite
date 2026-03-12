# Release Readiness Workflow

Owners: release, engine
Last verified against commit: 071697a

## Deterministic Gates

```bash
uv run ruff check .
uv run --no-sync pytest -q
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --full --output json
uv run --no-sync ralphite check --workspace /tmp/ralphite-strict-check --strict --output json
```

## Policy

- codex backend is required
- cursor backend is optional unless explicitly selected for target environments
- strict checks should not rely on runtime simulation fallback

## Real Backend Sign-Off

Run without skip env flags and capture command outputs.

## Manual Cold-Start Verification

Run a fresh temp-workspace walkthrough:

```bash
tmpdir="$(mktemp -d /tmp/ralphite-release-XXXXXX)"
uv run ralphite quickstart --workspace "$tmpdir" --yes --output table
```

Confirm that:

- `doctor` shows a healthy readiness table
- `quickstart` prints plan/backend/model/capability preflight details
- non-JSON output surfaces run id, next action, and artifact paths
- raw wildcard literals such as `tool:*` / `mcp:*` do not leak into operator-facing CLI text
- reconcile/salvage/promote-salvage commands are available and documented in operator output paths

## Git Runtime Readiness

Release verification should treat git prerequisites as two separate checks:

- Repository ready: inside a git worktree with an initial commit
- Execution ready: repository ready and clean enough to launch a new run

Operator expectations:

- `run` and `quickstart` fail closed when execution readiness is missing.
- `recover` and `replay` are allowed on a dirty workspace, but they should surface a warning and continue to real recovery/replay validation.
- A git prerequisite failure should be reported as an environment issue, not disguised as a product/runtime defect.

## Recovery and Salvage Sign-Off

Release verification should confirm:

- `reconcile --apply` can be referenced as the supported repair path for state drift
- `salvage` describes retained work without requiring manual JSON edits
- `promote-salvage` is documented for both committed and dirty retained worker work

## Cross-Platform Runtime Notes

Release verification should confirm:

- acceptance commands are documented as direct argv execution, not shell-dependent behavior
- Windows/hostile consoles can fall back to ASCII-safe stream output when Rich rendering cannot encode cleanly

## Sign-Off Artifact

Record in release notes:

- timestamp (local + UTC)
- commit SHA
- executed command list
- pass/fail outcomes
- cold-start verification outcome
- any waived warnings + rationale
