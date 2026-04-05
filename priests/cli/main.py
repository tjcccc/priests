from __future__ import annotations

import typer

from priests import __version__
from priests.cli.init_cmd import init_command
from priests.cli.run_cmd import run_app
from priests.cli.profile_cmd import profile_app
from priests.cli.config_cmd import config_app
from priests.cli.model_cmd import model_app
from priests.cli.provider_cmd import provider_app
from priests.cli.service_cmd import service_app


app = typer.Typer(
    name="priests",
    help="AI dispatch CLI and service.",
    no_args_is_help=True,
)

app.command("init")(init_command)
app.add_typer(run_app, name="run")
app.add_typer(profile_app, name="profile")
app.add_typer(config_app, name="config")
app.add_typer(model_app, name="model")
app.add_typer(provider_app, name="provider")
app.add_typer(service_app, name="service")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", is_eager=True, help="Show version and exit."),
) -> None:
    if version:
        typer.echo(f"priests {__version__}")
        raise typer.Exit()
