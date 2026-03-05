# Docs Maintenance Workflow

Owners: release, engine, cli, schemas
Last verified against commit: 70b0c1f

## Update Triggers

Update docs in the same PR when changing:

- CLI flags/command behavior
- backend command templates
- schema defaults/enums
- gate suite composition
- recovery or exit-code semantics

## Required Checks

```bash
uv run --no-sync pytest tests/engine/test_docs_knowledge_base.py -q
uv run --no-sync pytest tests/engine/test_headless_agent.py tests/cli/test_cli_ux_commands.py -q
```

## ADR Trigger

Add/update ADR in `docs/decisions/` for architecture/runtime contract changes.
