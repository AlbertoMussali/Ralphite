# User Guide

Last verified against commit: 071697a

Ralphite is a local-first multi-agent orchestrator. Use this guide to understand the core concepts and how to execute your first plan.

> [!IMPORTANT]
> Canonical starting points:
> - Operators: [README.md](README.md) -> [docs/workflows/first-run.md](docs/workflows/first-run.md)
> - Contributors: [CONTRIBUTING.md](CONTRIBUTING.md) -> [docs/index.md](docs/index.md)
>
> This page is a compact conceptual refresher, not the primary entrypoint.

## Core Concepts

### 1. Workspaces
A Ralphite workspace is a directory containing a `.ralphite` folder. This folder holds your configuration (`config.toml`) and your plans (`plans/`). 
> [!IMPORTANT]
> Ralphite requires your workspace to be inside a **git repository**.

### 2. Plans (v1 YAML)
Plans define the sequence of tasks for the agents. They live in `.ralphite/plans/` and follow the `version: 1` schema.
A plan typically includes:
- **Goals**: What the plan aims to achieve.
- **Tasks**: Individual steps with acceptance criteria.
- **Orchestration**: The flow between tasks (linear, branched, or loops).

### 3. Backends
Ralphite supports multiple execution backends:
- **Codex**: The default headless agent (uses `gpt-5.3-codex`).
- **Cursor**: Leverages the Cursor agent command.

### 4. Recovery Truth
Ralphite treats git/worktree state as the source of truth for what changed.

- Backend payloads and summaries are useful diagnostics.
- `reconcile` rebuilds run truth from persisted state plus live git/worktree state.
- `reconcile --apply` persists repaired cached state.

### 5. Retained Work
Failed or interrupted runs preserve managed work by default so useful output is not lost.

- `salvage` inventories preserved work and salvage metadata.
- `promote-salvage` can promote retained worker work back through acceptance and integration.

### 6. Write Policy
Tasks may declare `write_policy` to restrict what roots a worker can mutate.

- `allowed_write_roots`
- `forbidden_write_roots`
- `allow_plan_edits`
- `allow_root_writes`

Observed local writes outside scope are rejected even if the backend summary claims success.

## Quick Commands (After Canonical Start)

### Installation
Ralphite is designed to be run with `uv`:
```bash
# Recommended: start from a git repo
git init -b main
git add . && git commit -m "initial"

# Initialize Ralphite
uv run ralphite init --workspace .
```

### The "Happy Path"
After following the canonical start path in `README.md` (operators) or `CONTRIBUTING.md` + `docs/index.md` (contributors), use `quickstart` for environment checking, bootstrapping, and execution:
```bash
uv run ralphite quickstart --workspace . --yes
```

## Troubleshooting

If things aren't working as expected, use the `doctor` command:
```bash
uv run ralphite doctor --workspace . --output table
```

Common issues:
- **Git missing**: Ralphite needs git to manage worktrees for safe agent execution.
- **Backend missing**: Ensure `codex` or `cursor` is in your `PATH`.
- **Plan validation**: Use `uv run ralphite validate` to check your YAML files.
- **State drift after a failed run**: Use `reconcile --apply`, then inspect retained work with `salvage`.

For full CLI usage, see the [CLI Reference](docs/references/cli-contracts.md).
