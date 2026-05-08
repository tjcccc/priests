from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

import questionary

from priests.cli.init_cmd import _arrow_select
from priests.config.loader import load_config, save_config

profile_app = typer.Typer(help="Manage profiles.")
console = Console()
err_console = Console(stderr=True)


@profile_app.callback(invoke_without_command=True)
def profile_root(
    ctx: typer.Context,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Show the current default profile, or run a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    config = load_config(config_file)
    console.print(f"Current profile: {config.default.profile}")

_PROFILE_MD_STUB = """\
# {name}

You are a helpful assistant.
"""

_PROFILE_TOML_STUB = """\
# Profile settings for {name}

# Set to false to disable memory loading and saving for this profile.
# Useful for tool profiles (dictionary, formatter, etc.) that don't need user memory.
memories = true

# Optional model override. Leave unset to use the global default model.
# provider = "bailian"
# model = "qwen-plus"

# Override the global memory size limit for this profile (max characters in auto_short.md).
# Uncomment to override.
# memories_limit = 50000
"""

_RULES_MD_STUB = """\
# Rules

Be honest. Do not make things up.
Be concise unless the user asks for depth.

Replace this content with specific guidance for this profile's role.
"""


@profile_app.command("list")
def profile_list(
    profiles_dir: Annotated[Path | None, typer.Option("--profiles-dir", help="Profiles directory.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """List available profiles."""
    config = load_config(config_file)
    root = (profiles_dir or config.paths.profiles_dir).expanduser()

    if not root.exists():
        console.print(f"[dim]Profiles directory not found: {root}[/dim]")
        console.print("[dim]Use 'priests profile init NAME' to create your first profile.[/dim]")
        return

    profiles = sorted(
        d.name for d in root.iterdir() if d.is_dir() and (d / "PROFILE.md").exists()
    )

    if not profiles:
        console.print(f"[dim]No profiles found in {root}[/dim]")
        return

    table = Table(show_header=False, box=None, pad_edge=False)
    for name in profiles:
        table.add_row(f"  {name}")

    console.print(table)


@profile_app.command("init")
def profile_init(
    name: Annotated[str | None, typer.Argument(help="Profile name to create.")] = None,
    profiles_dir: Annotated[Path | None, typer.Option("--profiles-dir", help="Profiles directory.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Scaffold a new profile directory."""
    if name is None:
        name = typer.prompt("Profile name").strip()
    if not name:
        err_console.print("[red]Profile name cannot be empty.[/red]")
        raise typer.Exit(1)
    config = load_config(config_file)
    root = (profiles_dir or config.paths.profiles_dir).expanduser()
    profile_dir = root / name

    if profile_dir.exists():
        err_console.print(f"[red]Profile '{name}' already exists at {profile_dir}[/red]")
        raise typer.Exit(1)

    from priests.engine_factory import _scaffold_memories
    profile_dir.mkdir(parents=True)
    (profile_dir / "PROFILE.md").write_text(_PROFILE_MD_STUB.format(name=name))
    (profile_dir / "RULES.md").write_text(_RULES_MD_STUB)
    (profile_dir / "CUSTOM.md").write_text("")
    (profile_dir / "profile.toml").write_text(_PROFILE_TOML_STUB.format(name=name))
    _scaffold_memories(profile_dir / "memories")

    console.print(f"[green]Created profile '{name}'[/green] at {profile_dir}")
    console.print(f"  Edit [bold]{profile_dir / 'PROFILE.md'}[/bold] to define the identity.")


@profile_app.command("default")
def profile_default(
    name: Annotated[str | None, typer.Argument(help="Profile name to set as default. Omit to choose interactively.")] = None,
    profiles_dir: Annotated[Path | None, typer.Option("--profiles-dir", help="Profiles directory.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Set the default profile used by 'priests run'."""
    config = load_config(config_file)
    root = (profiles_dir or config.paths.profiles_dir).expanduser()

    available = sorted(
        d.name for d in root.iterdir() if d.is_dir() and (d / "PROFILE.md").exists()
    ) if root.exists() else []

    if not available:
        err_console.print("[yellow]No profiles found.[/yellow] Run [bold]priests profile init NAME[/bold] first.")
        raise typer.Exit(1)

    if name is None:
        choices = [
            questionary.Choice(
                title=f"{'* ' if p == config.default.profile else '  '}{p}",
                value=p,
            )
            for p in available
        ]
        name = _arrow_select("Select default profile:", choices)

    elif name not in available:
        err_console.print(f"[red]Profile '{name}' not found.[/red] Use 'priests profile list' to see available profiles.")
        raise typer.Exit(1)

    config.default.profile = name
    save_config(config, config_file)
    console.print(f"[green]Default profile set to '{name}'.[/green]")
