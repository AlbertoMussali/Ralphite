# ADR-0002: Strict Check Policy

- Status: Accepted
- Date: 2026-03-05
- Owners: release, engine

## Context

A single deterministic release signal is needed for release readiness.

## Decision

`check --strict` requires:

1. doctor success (blocking checks)
2. no doctor hygiene warnings for recoverable runs or stale managed artifacts
3. backend smoke success for selected default backend
4. all strict check suites passing

Cursor support is optional unless selected in config/CLI.

## Alternatives Considered

1. Keep strict checks equivalent to previous (less strict) release suites-only behavior.
2. Require both codex and cursor always.
3. Remove backend smoke from gate.

## Consequences

- Stronger readiness confidence.
- Slightly stricter local environment requirements.
- Repeated operator loops now fail strict checks until stale recovery state is resolved.

## Rollback / Migration Plan

Adjust policy only through ADR update and matching CLI/test/docs changes.

## References

- `src/ralphite/cli/commands/check_cmd.py`
- `tests/cli/test_cli_ux_commands.py`
