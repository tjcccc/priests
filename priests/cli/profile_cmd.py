from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from priests.config.loader import load_config

profile_app = typer.Typer(help="Manage profiles.")
console = Console()
err_console = Console(stderr=True)

_PROFILE_MD_STUB = """\
# {name}

You are a helpful assistant.
"""

_RULES_MD_STUB = """\
# Rules

Be honest. Do not make things up.
Be concise unless the user asks for depth.

## Memory

Define what this profile should remember about the user.
Replace this line with a description — e.g. "Remember the user's name, preferences,
and any personal details they share."
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
    name: Annotated[str, typer.Argument(help="Profile name to create.")],
    profiles_dir: Annotated[Path | None, typer.Option("--profiles-dir", help="Profiles directory.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Scaffold a new profile directory."""
    config = load_config(config_file)
    root = (profiles_dir or config.paths.profiles_dir).expanduser()
    profile_dir = root / name

    if profile_dir.exists():
        err_console.print(f"[red]Profile '{name}' already exists at {profile_dir}[/red]")
        raise typer.Exit(1)

    profile_dir.mkdir(parents=True)
    (profile_dir / "PROFILE.md").write_text(_PROFILE_MD_STUB.format(name=name))
    (profile_dir / "RULES.md").write_text(_RULES_MD_STUB)
    (profile_dir / "CUSTOM.md").write_text("")
    (profile_dir / "memories").mkdir()

    console.print(f"[green]Created profile '{name}'[/green] at {profile_dir}")
    console.print(f"  Edit [bold]{profile_dir / 'PROFILE.md'}[/bold] to define the identity.")
