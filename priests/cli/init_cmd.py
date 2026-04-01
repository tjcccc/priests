from __future__ import annotations

from pathlib import Path
from typing import Annotated

import httpx
import questionary
import typer
from rich.console import Console

from priests.config.loader import is_initialized, save_config
from priests.config.model import (
    AppConfig,
    DefaultsConfig,
    OllamaConfig,
    PathsConfig,
    ProvidersConfig,
    ServiceConfig,
)
from priests.engine_factory import _bootstrap_profiles

console = Console()
err_console = Console(stderr=True)

# Ordered list — append new providers here as they are implemented
_PROVIDERS: list[tuple[str, str]] = [
    ("ollama", "Local models via Ollama"),
    # ("openai",     "OpenAI API"),
    # ("gemini",     "Google Gemini"),
    # ("claude",     "Anthropic Claude"),
    # ("bailian",    "Alibaba Bailian"),
    # ("openrouter", "OpenRouter (multi-provider)"),
]


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
            console.print("  e.g. [bold]ollama pull qwen3.5:9b[/bold]\n")
            model = typer.prompt("Or enter a model name manually").strip()
            return model, base_url

        model = _arrow_select(
            "Select model:",
            [questionary.Choice(title=m) for m in models],
        )
        return model, base_url


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
    provider = _arrow_select(
        "Select a provider for initialization:",
        [questionary.Choice(title=f"{name}  —  {desc}", value=name) for name, desc in _PROVIDERS],
    )
    console.print()

    # --- Model (provider-specific) ---
    ollama_base_url = "http://localhost:11434"

    if provider == "ollama":
        model, ollama_base_url = _select_ollama_model(ollama_base_url)
    else:
        # Generic free-text for providers not yet implemented
        model = typer.prompt("Model name").strip()

    console.print()

    # --- Paths (show defaults, allow override) ---
    default_profiles_dir = str(Path.home() / ".priests" / "profiles")
    default_sessions_db = str(Path.home() / ".priests" / "sessions.db")

    profiles_dir_str = typer.prompt("Profiles directory", default=default_profiles_dir)
    sessions_db_str = typer.prompt("Sessions database", default=default_sessions_db)

    # --- Build and save config ---
    config = AppConfig(
        default=DefaultsConfig(provider=provider, model=model),
        paths=PathsConfig(
            profiles_dir=Path(profiles_dir_str),
            sessions_db=Path(sessions_db_str),
        ),
        service=ServiceConfig(),
        providers=ProvidersConfig(ollama=OllamaConfig(base_url=ollama_base_url)),
    )

    saved_path = save_config(config, config_file)

    # --- Bootstrap profiles ---
    profiles_root = Path(profiles_dir_str).expanduser()
    _bootstrap_profiles(profiles_root)

    console.print(f"\n[green]Initialized![/green] Config saved to {saved_path}")
    console.print(f"  provider : {provider}")
    console.print(f"  model    : {model}")
    console.print(f"  profiles : {profiles_root}")
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  [bold]priests run[/bold]                          start an interactive chat")
    console.print("  [bold]priests run --prompt \"...\"[/bold]           send a single prompt")
    console.print("  [bold]priests profile init \"my_profile\"[/bold]    create a custom profile")
    console.print("  [bold]priests models add[/bold]                    add more providers")
    console.print("  [bold]priests --help[/bold]  /  [bold]priests <command> --help[/bold]")
