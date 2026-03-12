from __future__ import annotations

import typer

from .commands import (
    cleanup_command,
    check_command,
    doctor_command,
    history_command,
    init_command,
    promote_salvage_command,
    quickstart_command,
    reconcile_command,
    recover_command,
    replay_command,
    run_command,
    salvage_command,
    validate_command,
    watch_command,
)

app = typer.Typer(
    help="Ralphite terminal-first orchestrator",
    no_args_is_help=True,
    add_completion=False,
)

app.command(name="init")(init_command)
app.command(name="quickstart")(quickstart_command)
app.command(name="validate")(validate_command)
app.command(name="doctor")(doctor_command)
app.command(name="run")(run_command)
app.command(name="watch")(watch_command)
app.command(name="recover")(recover_command)
app.command(name="history")(history_command)
app.command(name="replay")(replay_command)
app.command(name="check")(check_command)
app.command(name="cleanup")(cleanup_command)
app.command(name="salvage")(salvage_command)
app.command(name="reconcile")(reconcile_command)
app.command(name="promote-salvage")(promote_salvage_command)
