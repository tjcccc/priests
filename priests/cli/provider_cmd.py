from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from typer.core import TyperGroup
from rich.console import Console
from rich.table import Table

from priests.cli.init_cmd import _fetch_ollama_models, _fetch_openai_compat_models
from priests.config.loader import load_config
from priests.provider_status import (
    delete_ollama_model,
    fetch_ollama_model_records,
    provider_base_url,
    provider_status,
)
from priests.registry import get_provider, list_providers

console = Console()
err_console = Console(stderr=True)


class _ProviderGroup(TyperGroup):
    """Route `priests provider <name> [list]` to the hidden _provider_models command."""

    def resolve_command(self, ctx, args: list) -> tuple:
        cmd_name = args[0] if args else None
        if cmd_name and cmd_name not in self.commands:
            provider_name = args.pop(0)
            # Consume trailing 'list' keyword if present
            if args and args[0] == "list":
                args.pop(0)
            args.insert(0, "_provider_models")
            args.insert(1, provider_name)
        return super().resolve_command(ctx, args)


provider_app = typer.Typer(help="Manage providers.", cls=_ProviderGroup)


@provider_app.callback(invoke_without_command=True)
def provider_root(
    ctx: typer.Context,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Show the current default provider, or run a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    config = load_config(config_file)
    console.print(f"Current provider: {config.default.provider or '(none set)'}")


@provider_app.command("list")
def provider_list(
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """List all supported providers."""
    config = load_config(config_file)

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False, min_width=60)
    table.add_column("Provider", style="bold", min_width=16)
    table.add_column("Label")
    table.add_column("Configured", justify="center", min_width=12)

    for info in list_providers():
        if info.provider_type == "local":
            configured = "[green]local[/green]"
        else:
            cfg = getattr(config.providers, info.name, None)
            configured = "[green]yes[/green]" if (cfg and getattr(cfg, "api_key", None)) else "[dim]no[/dim]"

        table.add_row(info.name, info.label, configured)

    console.print(table)
    console.print(f"\n[dim]Run [bold]priests provider <name> list[/bold] to list models.[/dim]")


@provider_app.command("status")
def provider_status_cmd(
    name: Annotated[str | None, typer.Argument(help="Optional provider name to check.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Show provider configuration and local reachability status."""
    config = load_config(config_file)
    names = [name] if name else [p.name for p in list_providers()]

    for provider_name in names:
        if provider_name not in {p.name for p in list_providers()}:
            err_console.print(f"[red]Unknown provider:[/red] {provider_name}")
            raise typer.Exit(1)

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Provider", style="bold")
    table.add_column("Type")
    table.add_column("Configured", justify="center")
    table.add_column("Reachable", justify="center")
    table.add_column("Models", justify="right")
    table.add_column("Message")

    for provider_name in names:
        status = provider_status(config, provider_name)
        configured = "[green]yes[/green]" if status.configured else "[dim]no[/dim]"
        if status.reachable is True:
            reachable = "[green]yes[/green]"
        elif status.reachable is False:
            reachable = "[red]no[/red]"
        else:
            reachable = "[dim]n/a[/dim]"
        model_count = str(status.model_count) if status.model_count is not None else ""
        table.add_row(status.name, status.provider_type, configured, reachable, model_count, status.message)

    console.print(table)


@provider_app.command("storage")
def provider_storage(
    provider: Annotated[str, typer.Option("--provider", help="Local provider to inspect.")] = "ollama",
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """List local model storage details where supported."""
    if provider != "ollama":
        err_console.print("[red]Storage listing currently supports only Ollama.[/red]")
        raise typer.Exit(1)

    config = load_config(config_file)
    base_url = provider_base_url(config, provider)
    records = fetch_ollama_model_records(base_url)
    if records is None:
        err_console.print(f"[red]Could not connect to Ollama at {base_url}[/red]")
        raise typer.Exit(1)
    if not records:
        console.print("[dim]No Ollama models found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Model", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("Digest")
    for record in sorted(records, key=lambda r: str(r.get("name", ""))):
        size = int(record.get("size") or 0)
        digest = str(record.get("digest") or "")
        table.add_row(str(record.get("name", "")), _format_bytes(size), digest[:16])
    console.print(table)


@provider_app.command("delete-local-model")
def provider_delete_local_model(
    model: Annotated[str, typer.Argument(help="Local model name to delete, e.g. qwen3:8b.")],
    provider: Annotated[str, typer.Option("--provider", help="Local provider to mutate.")] = "ollama",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Delete without an interactive confirmation.")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Delete a local model where supported."""
    if provider != "ollama":
        err_console.print("[red]Local model deletion currently supports only Ollama.[/red]")
        raise typer.Exit(1)

    config = load_config(config_file)
    base_url = provider_base_url(config, provider)
    records = fetch_ollama_model_records(base_url)
    if records is None:
        err_console.print(f"[red]Could not connect to Ollama at {base_url}[/red]")
        raise typer.Exit(1)
    available = {str(record.get("name", "")) for record in records}
    if model not in available:
        err_console.print(f"[red]Ollama model not found:[/red] {model}")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"Delete Ollama model {model!r} from {base_url}?"):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit()

    try:
        delete_ollama_model(base_url, model)
    except Exception as exc:
        err_console.print(f"[red]Delete failed:[/red] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]Deleted Ollama model:[/green] {model}")


@provider_app.command("_provider_models", hidden=True)
def provider_models(
    name: Annotated[str, typer.Argument(help="Provider name (e.g. openai, groq, ollama).")],
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """List known models for a provider."""
    info = get_provider(name)
    if info is None:
        err_console.print(f"[red]Unknown provider:[/red] {name}")
        err_console.print(f"[dim]Run [bold]priests provider list[/bold] to see available providers.[/dim]")
        raise typer.Exit(1)

    console.print(f"[bold]{info.name}[/bold]  —  {info.label}\n")

    if info.known_models is None:
        # Local providers: fetch dynamically.
        config = load_config(config_file)
        cfg = getattr(config.providers, name)
        base_url = cfg.base_url
        console.print(f"[dim]Fetching models from {base_url} ...[/dim]")
        models = _fetch_ollama_models(base_url) if name == "ollama" else _fetch_openai_compat_models(base_url)
        if models is None:
            err_console.print(f"[red]Could not connect to {info.label} at {base_url}[/red]")
            raise typer.Exit(1)
        if not models:
            console.print("[yellow]No models found.[/yellow]")
            if name == "ollama":
                console.print("Pull one with [bold]ollama pull <model>[/bold].")
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


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"
