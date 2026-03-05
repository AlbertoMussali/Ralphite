# User Guide

Ralphite is a local-first multi-agent orchestrator. Use this guide to understand the core concepts and how to execute your first plan.

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

## Getting Started

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
For a quick start, use the `quickstart` command which combines environment checking, bootstrapping, and execution:
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

For full CLI usage, see the [CLI Reference](docs/references/cli-contracts.md).
