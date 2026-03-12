# Docs Maintenance Workflow

Owners: release, engine, cli, schemas
Last verified against commit: 071697a

## Update Triggers

Update docs in the same PR when changing:

- CLI flags/command behavior
- backend command templates
- schema defaults/enums
- gate suite composition
- recovery or exit-code semantics
- runtime authority, reconcile, salvage, or write-scope semantics

Also update in the same PR:

- generated snapshots under `docs/generated/` when command builders or schema summaries change
- freshness markers on every changed doc
- operator-facing workflow docs when recovery/runtime behavior changes

## Required Checks

```bash
uv run --no-sync pytest tests/engine/test_docs_knowledge_base.py -q
uv run --no-sync pytest tests/engine/test_headless_agent.py tests/cli/test_cli_ux_commands.py -q
```

## ADR Trigger

Add/update ADR in `docs/decisions/` for architecture/runtime contract changes.
