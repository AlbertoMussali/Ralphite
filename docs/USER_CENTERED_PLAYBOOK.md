# User-Centered Playbook

This playbook captures practical operator flows for Ralphite's local-first workflow.

## 1) First Run (Fast Path)

1. Initialize workspace:
   - `uv run ralphite init --workspace .`
2. Run guided setup:
   - `uv run ralphite quickstart --workspace . --no-tui --yes --output stream`
3. If you prefer interactive setup:
   - `uv run ralphite tui --workspace .`
   - Open `Run Setup` and validate before start.

## 2) Failed Run Recovery

1. Find recoverable run:
   - `uv run ralphite recover --workspace . --no-tui --preflight-only --output json`
2. Select recovery mode:
   - `manual` for explicit conflict resolution.
   - `agent_best_effort` with prompt for assisted recovery.
   - `abort_phase` to stop and summarize.
3. In TUI Recovery:
   - Run `Preflight`.
   - Use `Show Worktree` to open the path.
   - Use `Show Commands` to copy next commands.
   - Resume once checks pass.

## 3) Policy Hardening

Use Settings presets as a baseline:

- `Open`: maximum compatibility for local experimentation.
- `Balanced`: broad access with targeted denials.
- `Restricted`: deny-by-default for sensitive environments.

Recommended hardening loop:

1. Start with `Balanced`.
2. Run real workflows.
3. Move frequently unused capabilities to deny lists.
4. Save and rerun `doctor` + `check`.

## 4) JSON Automation Contract

Machine-readable mode uses a strict envelope:

- `schema_version: "cli-output.v1"`
- `command`
- `ok`, `status`, `run_id`, `exit_code`
- `issues`, `next_actions`
- `data`

Examples:

- `uv run ralphite run --workspace . --no-tui --output json`
- `uv run ralphite recover --workspace . --no-tui --output json`
- `uv run ralphite check --workspace . --output json`

`tui` requires `--dry-run` in JSON mode:

- `uv run ralphite tui --workspace . --output json --dry-run`

## 5) Troubleshooting Matrix

| Status Label | Likely Cause | Next Action |
|---|---|---|
| `Needs Recovery` | Merge/integration conflict or paused recovery state | Open Recovery, run preflight, choose mode, resume |
| `Failed` | Runtime, policy, or write-back failure | Check timeline failure summary and rerun failed path |
| `Paused` | Manual pause or pending resume | Resume run or inspect recovery checks |
| `Cancelled` | User-cancelled execution | Replay failed run if needed |
| `Succeeded` | Workflow completed | Review artifacts and summary |

## 6) Run Setup Safety Workflow

1. Validate plan in `Run Setup`.
2. Use `Apply Safe Fixes` to generate preview diff.
3. Choose `Accept Fixes` or `Reject Fixes`.
4. Confirm row badges (`Title`, `Deps`, `Agent`, `Routing`, `Acceptance`) are `OK`.
5. Save revision and start run.
