# First Run Workflow

Owners: cli
Last verified against commit: 70b0c1f

## Codex-First Path

```bash
uv run ralphite init --workspace .
uv run ralphite quickstart --workspace . --yes --output table
uv run ralphite run --workspace . --yes --output table
```

`init` creates the local Ralphite workspace.

- `.ralphite/config.toml`
- `.ralphite/plans/*.yaml`

`quickstart` is the recommended cold-start command.

- Runs `doctor` first.
- Bootstraps missing config or starter plans when `--bootstrap` is enabled.
- Shows a preflight summary with selected plan, backend, model, reasoning effort, and the capability scope requested by the selected plan.
- Starts execution and writes run artifacts on completion.
- Makes the transition from preflight to execution explicit so the first run does not look stalled.

`run` is the direct execution path once the workspace is already healthy.

## Expected Operator Output

During preflight, the CLI should make these items obvious before approval:

- Which plan will run
- Which backend/model will be used
- What capability scope the selected plan requests
- That approval applies to this run only
- That approval covers the tool and MCP access declared by the selected plan

On completion, the CLI should surface:

- Run status
- Run id
- Most relevant next action
- Key artifact paths

Typical artifacts:

- `.ralphite/artifacts/<run-id>/final_report.md`
- `.ralphite/artifacts/<run-id>/run_metrics.json`
- `.ralphite/artifacts/<run-id>/machine_bundle.json`

## Common Follow-Up Commands

Inspect environment and plan readiness:

```bash
uv run ralphite doctor --workspace . --output table
```

Inspect recent runs and failure signals:

```bash
uv run ralphite history --workspace . --output table
```

Recover a paused or failed run:

```bash
uv run ralphite recover --workspace . --output table
```

Validate plan structure before rerunning:

```bash
uv run ralphite validate --workspace . --json
```

## Optional Backend Override

```bash
uv run ralphite run --workspace . --backend codex --model gpt-5.3-codex --reasoning-effort medium --yes --output json
```

## Expected Outcome

- `quickstart` or `run` reaches `succeeded`, or returns a typed failure plus a concrete next action in the CLI output envelope.
