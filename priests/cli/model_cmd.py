from __future__ import annotations

from pathlib import Path
from typing import Annotated

import questionary
import typer
from rich.console import Console

from priests.cli.init_cmd import (
    _apply_provider_to_config,
    _arrow_select,
    _register_model,
    _select_model,
    _select_ollama_model,
)
from priests.config.loader import is_initialized, load_config, save_config
from priests.registry import list_providers

model_app = typer.Typer(help="Manage model defaults and provider setup.")
console = Console()
err_console = Console(stderr=True)


@model_app.callback(invoke_without_command=True)
def model_root(
    ctx: typer.Context,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Show the current default model, or run a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    config = load_config(config_file)
    provider = config.default.provider or "(none)"
    model = config.default.model or "(none)"
    console.print(f"Current model: {provider}/{model}")

_ADD_NEW = "__add_new__"


@model_app.command("list")
def model_list(
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """List all added models."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)

    config = load_config(config_file)
    if not config.models.options:
        console.print("[dim]No models added yet. Run [bold]priests model add[/bold].[/dim]")
        return

    for entry in config.models.options:
        marker = "[green]*[/green] " if entry == f"{config.default.provider}/{config.default.model}" else "  "
        console.print(f"{marker}{entry}")


@model_app.command("default")
def model_default(
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Set the default model from your added models list."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)

    config = load_config(config_file)

    if config.models.options:
        choices = [questionary.Choice(title=m) for m in config.models.options]
        choices.append(questionary.Choice(title="Add new model…", value=_ADD_NEW))
        selected = _arrow_select("Select default model:", choices)
    else:
        selected = _ADD_NEW

    if selected == _ADD_NEW:
        provider_name, model = _run_add_flow(config, config_file)
    else:
        provider_name, model = selected.split("/", 1)

    config.default.provider = provider_name
    config.default.model = model

    saved_path = save_config(config, config_file)
    console.print(f"\n[green]Default updated.[/green] Saved to {saved_path}")
    console.print(f"  {provider_name}/{model}")


@model_app.command("add")
def model_add(
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Configure an additional provider and add a model to your list."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)

    config = load_config(config_file)
    provider_name, model = _run_add_flow(config, config_file)

    console.print(f"\n[green]Model added.[/green]  {provider_name}/{model}")
    console.print(f"[dim]Use with: priests run --provider {provider_name} --model {model}[/dim]")
    console.print(f"[dim]Set as default: priests model default[/dim]")


def _run_add_flow(config, config_file) -> tuple[str, str]:
    """Shared flow: select provider, enter key, select model, save. Returns (provider, model)."""
    all_providers = list_providers()
    provider_name = _arrow_select(
        "Select provider:",
        [questionary.Choice(title=f"{p.name}  —  {p.label}", value=p.name) for p in all_providers],
    )
    console.print()

    info = next(p for p in all_providers if p.name == provider_name)

    if provider_name == "ollama":
        current_url = config.providers.ollama.base_url
        model, ollama_base_url = _select_ollama_model(current_url)
        config.providers.ollama.base_url = ollama_base_url
        api_key = ""
        custom_base_url = ""
    else:
        custom_base_url = ""
        if provider_name == "custom":
            current_url = config.providers.custom.base_url if config.providers.custom else ""
            custom_base_url = typer.prompt("Base URL", default=current_url or "https://").strip().rstrip("/")
        api_key = ""
        if info.needs_api_key:
            api_key = typer.prompt("API key").strip()
        model = _select_model(info)

    _apply_provider_to_config(config.providers, provider_name, api_key, custom_base_url)
    _register_model(config, provider_name, model)
    save_config(config, config_file)

    return provider_name, model
