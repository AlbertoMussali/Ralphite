# Ralphite

Ralphite is a local-first, CLI-first multi-agent execution system for **v5 YAML plans**.

Last verified against commit: 70b0c1f

## 5-Minute Quickstart

Requirements: Python 3.12+, `uv`, `git`, `rg`, and `codex` in PATH.

```bash
uv run ralphite init --workspace .
uv run ralphite quickstart --workspace . --yes --output stream
uv run ralphite run --workspace . --yes --output stream
```

## What To Read Next

- Doc hub: [docs/index.md](docs/index.md)
- User workflows: [docs/workflows/index.md](docs/workflows/index.md)
- Architecture detail: [docs/architecture/index.md](docs/architecture/index.md)
- CLI and schema references: [docs/references/index.md](docs/references/index.md)
- Release readiness runbook: [docs/workflows/release-readiness.md](docs/workflows/release-readiness.md)

## Defaults and Compatibility

- Default backend: `codex`
- Optional backend: `cursor` (`agent` command)
- Default model: `gpt-5.3-codex`
- Default reasoning effort: `medium`
- Plan runtime: `version: 5` only
- Legacy `provider: openai` remains migration-compatible and normalizes to codex behavior at runtime
