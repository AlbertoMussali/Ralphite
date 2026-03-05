from __future__ import annotations

import typer

from .commands import (
    check_command,
    doctor_command,
    history_command,
    init_command,
    quickstart_command,
    recover_command,
    replay_command,
    run_command,
    validate_command,
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
app.command(name="recover")(recover_command)
app.command(name="history")(history_command)
app.command(name="replay")(replay_command)
app.command(name="check")(check_command)
