from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from priests.cli.init_cmd import _fetch_ollama_models
from priests.config.loader import load_config
from priests.registry import get_provider, list_providers

providers_app = typer.Typer(help="List available providers and their models.")
console = Console()
err_console = Console(stderr=True)


@providers_app.callback(invoke_without_command=True)
def providers_list(
    ctx: typer.Context,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """List all supported providers."""
    if ctx.invoked_subcommand is not None:
        return

    config = load_config(config_file)

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False, min_width=60)
    table.add_column("Provider", style="bold", min_width=16)
    table.add_column("Label")
    table.add_column("Configured", justify="center", min_width=12)

    for info in list_providers():
        if info.name == "ollama":
            configured = "[green]local[/green]"
        else:
            cfg = getattr(config.providers, info.name, None)
            configured = "[green]yes[/green]" if (cfg and getattr(cfg, "api_key", None)) else "[dim]no[/dim]"

        table.add_row(info.name, info.label, configured)

    console.print(table)
    console.print(f"\n[dim]Run [bold]priests providers <name>[/bold] to list models.[/dim]")


@providers_app.command("models")
def provider_models(
    name: Annotated[str, typer.Argument(help="Provider name (e.g. openai, groq, ollama).")],
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """List known models for a provider."""
    info = get_provider(name)
    if info is None:
        err_console.print(f"[red]Unknown provider:[/red] {name}")
        err_console.print(f"[dim]Run [bold]priests providers[/bold] to see available providers.[/dim]")
        raise typer.Exit(1)

    console.print(f"[bold]{info.name}[/bold]  —  {info.label}\n")

    if info.known_models is None:
        # Ollama: fetch dynamically
        config = load_config(config_file)
        base_url = config.providers.ollama.base_url
        console.print(f"[dim]Fetching models from {base_url} ...[/dim]")
        models = _fetch_ollama_models(base_url)
        if models is None:
            err_console.print(f"[red]Could not connect to Ollama at {base_url}[/red]")
            raise typer.Exit(1)
        if not models:
            console.print("[yellow]No models found.[/yellow] Pull one with [bold]ollama pull <model>[/bold].")
            return
        for m in models:
            console.print(f"  {m}")
        return

    if not info.known_models:
        console.print("[dim]No curated model list — enter the model name manually.[/dim]")
        if name == "openrouter":
            console.print("[dim]Browse models at https://openrouter.ai/models[/dim]")
        return

    for m in info.known_models:
        console.print(f"  {m}")
    console.print(f"\n[dim]Use: [bold]priests run --provider {name} --model <model>[/bold][/dim]")
