# Release Success Criteria

Owners: release, engine
Last verified against commit: 70b0c1f

## Required

- Codex backend healthy and default path validated.
- Cursor backend documented as optional unless selected.
- v5 schema and template execution stable.
- Recovery paths produce actionable typed failures.
- Documentation hub complete and source-aligned.

## Metrics

- Gate pass rate: `uv run ralphite check --workspace . --full --output json` and `uv run ralphite check --workspace . --strict --output json` pass on `main`.
- Test coverage confidence: fixture matrix + examples + runtime failure tests pass.
- Docs freshness: docs checks green and ADR policy satisfied for architecture-impacting changes.
