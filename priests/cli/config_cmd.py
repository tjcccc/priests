from __future__ import annotations

from pathlib import Path
from typing import Annotated

import tomli_w
import typer
from rich.console import Console
from rich.syntax import Syntax

from priests.config.loader import is_initialized, load_config, set_config_value

config_app = typer.Typer(help="View and edit configuration.")
console = Console()
err_console = Console(stderr=True)


def _strip_none(obj):
    """Recursively remove None values — TOML has no null type."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    return obj


@config_app.command("show")
def config_show(
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Print the current resolved configuration."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)
    config = load_config(config_file)
    raw = _strip_none(config.model_dump(mode="json"))
    toml_str = tomli_w.dumps(raw)
    console.print(Syntax(toml_str, "toml", theme="ansi_dark", background_color="default"))


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. default.model or service.port.")],
    value: Annotated[str, typer.Argument(help="New value.")],
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Set a configuration value and save it."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)
    try:
        saved_path = set_config_value(key, value, config_file)
        console.print(f"[green]Set[/green] {key} = {value!r}")
        console.print(f"[dim]Saved to {saved_path}[/dim]")
    except KeyError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except (ValueError, TypeError) as e:
        err_console.print(f"[red]Invalid value:[/red] {e}")
        raise typer.Exit(1)
