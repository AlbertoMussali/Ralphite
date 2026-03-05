# ADR-0004: CLI-Only Hard Pivot

- Status: Accepted
- Date: 2026-03-05
- Owners: engine, cli, release

## Context

Ralphite previously mixed a Textual TUI with the CLI package boundary. Runtime command contracts and release gates were tied to TUI package paths, which increased maintenance overhead and produced a large single-file CLI surface.

## Decision

- Remove all Textual/TUI runtime paths and delete the `tui` command.
- Remove `--no-tui` flags from `quickstart`, `run`, `recover`, and `replay`.
- Extract and keep the stable command surface in `apps/cli/src/ralphite_cli/`.
- Keep command envelope schema `cli-output.v1` because payload structure remains compatible.
- Update strict/full check suites to reference `apps/cli/tests` and no TUI paths.

## Alternatives Considered

1. Keep TUI as an optional plugin.
2. Keep `--no-tui` as compatibility alias.
3. Keep prior package naming/paths despite TUI removal.

## Consequences

- Simpler packaging and lower maintenance surface.
- Breaking CLI change for removed flags and removed command.
- Strict checks now validate CLI-only suites.

## Rollback Plan

Rollback requires restoring TUI code and CLI options, and updating docs + strict-check suites consistently.

## References

- `apps/cli/src/ralphite_cli/app.py`
- `apps/cli/src/ralphite_cli/commands/check_cmd.py`
- `apps/cli/tests/test_cli_hard_pivot_contract.py`
