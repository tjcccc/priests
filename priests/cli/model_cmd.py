from __future__ import annotations

from pathlib import Path
from typing import Annotated

import questionary
import tomli_w
import typer
from rich.console import Console

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from priests.cli.init_cmd import (
    _apply_provider_to_config,
    _arrow_select,
    _select_openai_compat_local_model,
    _register_model,
    _select_model,
    _select_ollama_model,
)
from priests.config.loader import is_initialized, load_config, save_config
from priests.config.model import AppConfig
from priests.provider_status import validate_model
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
_USE_GLOBAL_DEFAULT = "__use_global_default__"


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
    profile: Annotated[str | None, typer.Option("--profile", help="Set model override for this profile instead of the global default.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Set the global default model, or a profile model override with --profile."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)

    config = load_config(config_file)
    profile_dir: Path | None = None
    if profile:
        try:
            profile_dir = _require_profile_dir(config, profile)
        except ValueError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)

    if profile:
        default_label = _global_default_label(config)
        choices = [questionary.Choice(title=f"Use default ({default_label})", value=_USE_GLOBAL_DEFAULT)]
        choices.extend(questionary.Choice(title=m) for m in config.models.options)
        choices.append(questionary.Choice(title="Add new model…", value=_ADD_NEW))
        selected = _arrow_select(f"Select model for profile '{profile}':", choices)
    elif config.models.options:
        choices = []
        choices.extend(questionary.Choice(title=m) for m in config.models.options)
        choices.append(questionary.Choice(title="Add new model…", value=_ADD_NEW))
        selected = _arrow_select("Select default model:", choices)
    else:
        selected = _ADD_NEW

    if selected == _USE_GLOBAL_DEFAULT:
        assert profile_dir is not None
        _set_profile_model(profile_dir, None, None)
        console.print(f"\n[green]Profile model cleared.[/green] {profile} now uses {_global_default_label(config)}")
        return

    if selected == _ADD_NEW:
        provider_name, model = _run_add_flow(config, config_file)
    else:
        provider_name, model = selected.split("/", 1)

    if profile:
        assert profile_dir is not None
        _set_profile_model(profile_dir, provider_name, model)
        console.print(f"\n[green]Profile model updated.[/green] {profile}")
        console.print(f"  {provider_name}/{model}")
        return

    config.default.provider = provider_name
    config.default.model = model

    saved_path = save_config(config, config_file)
    console.print(f"\n[green]Default updated.[/green] Saved to {saved_path}")
    console.print(f"  {provider_name}/{model}")


@model_app.command("rm")
def model_rm(
    model: Annotated[str, typer.Argument(help="Model to remove, e.g. ollama/llava:7b.")],
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Remove a model from your list."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)

    config = load_config(config_file)

    if model not in config.models.options:
        err_console.print(f"[red]Model not found:[/red] {model}")
        raise typer.Exit(1)

    config.models.options.remove(model)

    is_default = f"{config.default.provider}/{config.default.model}" == model
    if is_default:
        config.default.provider = ""
        config.default.model = ""

    save_config(config, config_file)
    console.print(f"[green]Removed:[/green] {model}")
    if is_default:
        console.print("[yellow]That was your default model.[/yellow] Run [bold]priests model default[/bold] to set a new one.")


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


@model_app.command("validate")
def model_validate(
    model: Annotated[str | None, typer.Argument(help="Model to validate, e.g. ollama/qwen3:8b. Omit to validate the default.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Validate a provider/model pair against config and reachable local models."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)

    config = load_config(config_file)
    if model is None:
        if not config.default.provider or not config.default.model:
            err_console.print("[red]No default model is configured.[/red]")
            raise typer.Exit(1)
        provider_name = config.default.provider
        model_name = config.default.model
    else:
        if "/" not in model:
            err_console.print("[red]Invalid model format.[/red] Use provider/model, e.g. ollama/qwen3:8b")
            raise typer.Exit(1)
        provider_name, model_name = model.split("/", 1)

    result = validate_model(config, provider_name, model_name)
    style = "green" if result.status == "ok" else "yellow" if result.status == "warning" else "red"
    console.print(f"[{style}]{result.status.upper()}[/{style}] {provider_name}/{model_name}: {result.message}")
    if not result.valid:
        raise typer.Exit(1)


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
    elif info.provider_type == "local" and info.known_models is None:
        current_cfg = getattr(config.providers, provider_name)
        model, base_url = _select_openai_compat_local_model(info.label, current_cfg.base_url)
        current_cfg.base_url = base_url
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


def _global_default_label(config: AppConfig) -> str:
    if config.default.provider and config.default.model:
        return f"{config.default.provider}/{config.default.model}"
    return "none"


def _require_profile_dir(config: AppConfig, profile: str) -> Path:
    root = config.paths.profiles_dir.expanduser()
    profile_dir = root / profile
    if not profile_dir.is_dir():
        raise ValueError(f"Profile not found: {profile}")
    return profile_dir


def _set_profile_model(profile_dir: Path, provider: str | None, model: str | None) -> None:
    toml_path = profile_dir / "profile.toml"
    data: dict = {}
    if toml_path.exists():
        data = dict(tomllib.loads(toml_path.read_text(encoding="utf-8")))

    if provider and model:
        data["provider"] = provider
        data["model"] = model
    else:
        data.pop("provider", None)
        data.pop("model", None)

    toml_path.write_bytes(tomli_w.dumps(data).encode())
