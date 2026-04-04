from __future__ import annotations

from pathlib import Path
from typing import Annotated

import httpx
import questionary
import typer
from rich.console import Console

from priests.config.loader import is_initialized, save_config
from priests.config.model import (
    AnthropicConfig,
    AppConfig,
    DefaultsConfig,
    OllamaConfig,
    OpenAICompatConfig,
    PathsConfig,
    ProvidersConfig,
    ServiceConfig,
)
from priests.engine_factory import _bootstrap_profiles
from priests.registry import ProviderInfo, get_provider, list_providers

console = Console()
err_console = Console(stderr=True)


def _arrow_select(prompt: str, choices: list[questionary.Choice]) -> str:
    """Arrow-key selection. Aborts with a clean message on Ctrl-C."""
    result = questionary.select(prompt, choices=choices, use_shortcuts=False).ask()
    if result is None:
        raise typer.Abort()
    return result


def _fetch_ollama_models(base_url: str) -> list[str] | None:
    """Return sorted model names from Ollama, or None if unreachable."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5.0)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        return None


def _select_ollama_model(default_url: str) -> tuple[str, str]:
    """Return (model_name, confirmed_base_url). Retries on bad URL."""
    base_url = default_url

    while True:
        console.print(f"[dim]Connecting to Ollama at {base_url} ...[/dim]")
        models = _fetch_ollama_models(base_url)

        if models is None:
            err_console.print(f"[red]Could not connect to Ollama at {base_url}[/red]")
            base_url = typer.prompt("Enter Ollama base URL").strip().rstrip("/")
            continue

        if not models:
            console.print("[yellow]No local models found.[/yellow] Make sure you have pulled at least one model.")
            console.print("  e.g. [bold]ollama pull qwen3:8b[/bold]\n")
            model = typer.prompt("Or enter a model name manually").strip()
            return model, base_url

        model = _arrow_select(
            "Select model:",
            [questionary.Choice(title=m) for m in models],
        )
        return model, base_url


def _select_model(info: ProviderInfo) -> str:
    """Select a model for a non-Ollama provider.

    Shows an arrow selector when known_models is non-empty, with an
    'Enter manually' escape hatch. Falls back to free-text when the list
    is empty (OpenRouter, Custom).
    """
    if not info.known_models:
        return typer.prompt("Model name").strip()

    _MANUAL = "__manual__"
    choices = [questionary.Choice(title=m) for m in info.known_models]
    choices.append(questionary.Choice(title="Enter manually…", value=_MANUAL))

    selected = _arrow_select("Select model:", choices)
    if selected == _MANUAL:
        return typer.prompt("Model name").strip()
    return selected


def _register_model(config: AppConfig, provider: str, model: str) -> None:
    """Add provider/model to config.models.options if not already present."""
    entry = f"{provider}/{model}"
    if entry not in config.models.options:
        config.models.options.append(entry)


def _apply_provider_to_config(
    providers: ProvidersConfig,
    provider: str,
    api_key: str,
    custom_base_url: str,
) -> None:
    """Write api_key (and base_url for custom) into the providers config in-place."""
    if provider == "anthropic":
        providers.anthropic = AnthropicConfig(api_key=api_key)
    elif provider == "custom":
        providers.custom = OpenAICompatConfig(api_key=api_key, base_url=custom_base_url)
    elif provider != "ollama":
        info = get_provider(provider)
        base_url = info.default_base_url if info else ""
        setattr(providers, provider, OpenAICompatConfig(api_key=api_key, base_url=base_url))


def init_command(
    force: Annotated[bool, typer.Option("--force", help="Re-initialize even if already set up.")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Initialize priests: configure provider, model, and scaffold profiles."""
    if is_initialized(config_file) and not force:
        console.print("[yellow]priests is already initialized.[/yellow]")
        console.print("Run [bold]priests config show[/bold] to see current settings.")
        console.print("Use [bold]--force[/bold] to re-initialize.")
        raise typer.Exit()

    console.print("[bold]Welcome to priests![/bold]")
    console.print("Let's set up your configuration.\n")

    # --- Provider ---
    providers_list = list_providers()
    provider_name = _arrow_select(
        "Select a provider:",
        [questionary.Choice(title=f"{p.name}  —  {p.label}", value=p.name) for p in providers_list],
    )
    console.print()

    info = next(p for p in providers_list if p.name == provider_name)

    # --- API key + model ---
    ollama_base_url = "http://localhost:11434"
    api_key = ""
    custom_base_url = ""

    if provider_name == "ollama":
        model, ollama_base_url = _select_ollama_model(ollama_base_url)
    else:
        if provider_name == "custom":
            custom_base_url = typer.prompt("Base URL (e.g. https://my-server/v1)").strip().rstrip("/")
        if info.needs_api_key:
            api_key = typer.prompt("API key", hide_input=False).strip()
        model = _select_model(info)

    console.print()

    # --- Paths ---
    default_profiles_dir = str(Path.home() / ".priests" / "profiles")
    default_sessions_db = str(Path.home() / ".priests" / "sessions.db")

    profiles_dir_str = typer.prompt("Profiles directory", default=default_profiles_dir)
    sessions_db_str = typer.prompt("Sessions database", default=default_sessions_db)

    # --- Build and save config ---
    providers = ProvidersConfig(ollama=OllamaConfig(base_url=ollama_base_url))
    _apply_provider_to_config(providers, provider_name, api_key, custom_base_url)

    config = AppConfig(
        default=DefaultsConfig(provider=provider_name, model=model),
        paths=PathsConfig(
            profiles_dir=Path(profiles_dir_str),
            sessions_db=Path(sessions_db_str),
        ),
        service=ServiceConfig(),
        providers=providers,
    )

    _register_model(config, provider_name, model)
    saved_path = save_config(config, config_file)

    profiles_root = Path(profiles_dir_str).expanduser()
    _bootstrap_profiles(profiles_root)

    console.print(f"\n[green]Initialized![/green] Config saved to {saved_path}")
    console.print(f"  provider : {provider_name}")
    console.print(f"  model    : {model}")
    console.print(f"  profiles : {profiles_root}")
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  [bold]priests run[/bold]                          start an interactive chat")
    console.print("  [bold]priests run --prompt \"...\"[/bold]           send a single prompt")
    console.print("  [bold]priests profile init \"my_profile\"[/bold]    create a custom profile")
    console.print("  [bold]priests model add[/bold]                     configure an additional provider")
    console.print("  [bold]priests --help[/bold]  /  [bold]priests <command> --help[/bold]")
