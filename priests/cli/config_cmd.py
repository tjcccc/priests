from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Annotated
from zipfile import ZipFile

import tomli_w
import typer
from rich.console import Console
from rich.syntax import Syntax

from priests.config.loader import is_initialized, load_config, save_config, set_config_value
from priests.config.model import AppConfig

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


@config_app.command("export")
def config_export(
    output: Annotated[Path, typer.Argument(help="Output .zip archive path.")],
    include_secrets: Annotated[bool, typer.Option("--include-secrets", help="Include API keys and OAuth tokens.")] = False,
    include_profiles: Annotated[bool, typer.Option("--profiles/--no-profiles", help="Include profile directories.")] = True,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing archive.")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Export config and profiles to a portable zip archive."""
    if not is_initialized(config_file):
        err_console.print("[yellow]Not initialized.[/yellow] Run [bold]priests init[/bold] first.")
        raise typer.Exit(1)

    output = output.expanduser()
    if output.exists() and not force:
        err_console.print(f"[red]Archive already exists:[/red] {output}")
        err_console.print("[dim]Use --force to overwrite it.[/dim]")
        raise typer.Exit(1)

    config = load_config(config_file)
    raw_config = _export_config_dict(config, include_secrets=include_secrets)

    output.parent.mkdir(parents=True, exist_ok=True)
    profile_files = 0
    with ZipFile(output, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "app": "priests",
                    "format": 1,
                    "includes_secrets": include_secrets,
                    "includes_profiles": include_profiles,
                },
                indent=2,
            ),
        )
        zf.writestr("config/priests.toml", tomli_w.dumps(raw_config))

        if include_profiles:
            profiles_root = config.paths.profiles_dir.expanduser()
            if profiles_root.exists():
                for file in sorted(profiles_root.rglob("*")):
                    if file.is_file() and not file.is_symlink():
                        rel = file.relative_to(profiles_root).as_posix()
                        zf.write(file, f"profiles/{rel}")
                        profile_files += 1

    console.print(f"[green]Exported:[/green] {output}")
    console.print(f"  secrets  : {'included' if include_secrets else 'stripped'}")
    console.print(f"  profiles : {profile_files} file(s)")


@config_app.command("import")
def config_import(
    archive: Annotated[Path, typer.Argument(help="Archive created by 'priests config export'.")],
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite existing config/profile files.")] = False,
    include_config: Annotated[bool, typer.Option("--include-config/--no-config", help="Import priests.toml from the archive.")] = True,
    include_profiles: Annotated[bool, typer.Option("--profiles/--no-profiles", help="Import profile files from the archive.")] = True,
    profiles_dir: Annotated[Path | None, typer.Option("--profiles-dir", help="Profiles directory override.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Import config and profiles from a portable zip archive."""
    archive = archive.expanduser()
    if not archive.exists():
        err_console.print(f"[red]Archive not found:[/red] {archive}")
        raise typer.Exit(1)

    current = load_config(config_file)
    target_config = config_file.expanduser() if config_file else Path.home() / ".priests" / "priests.toml"
    target_profiles = (profiles_dir or current.paths.profiles_dir).expanduser()

    imported_config = False
    imported_profiles = 0
    try:
        with ZipFile(archive) as zf:
            members = _safe_zip_members(zf)

            if include_config and "config/priests.toml" in members:
                if target_config.exists() and not overwrite:
                    err_console.print(f"[red]Config already exists:[/red] {target_config}")
                    err_console.print("[dim]Use --overwrite to replace it.[/dim]")
                    raise typer.Exit(1)
                raw = zf.read("config/priests.toml").decode("utf-8")
                imported = AppConfig.model_validate(_toml_loads(raw))
                save_config(imported, target_config)
                imported_config = True

            if include_profiles:
                for name in members:
                    if not name.startswith("profiles/") or name.endswith("/"):
                        continue
                    rel = PurePosixPath(name).relative_to("profiles")
                    target = target_profiles.joinpath(*rel.parts)
                    if target.exists() and not overwrite:
                        err_console.print(f"[red]Profile file already exists:[/red] {target}")
                        err_console.print("[dim]Use --overwrite to replace existing files.[/dim]")
                        raise typer.Exit(1)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(name))
                    imported_profiles += 1
    except typer.Exit:
        raise
    except Exception as exc:
        err_console.print(f"[red]Import failed:[/red] {exc}")
        raise typer.Exit(1)

    console.print(f"[green]Imported:[/green] {archive}")
    console.print(f"  config   : {'yes' if imported_config else 'no'}")
    console.print(f"  profiles : {imported_profiles} file(s)")


def _export_config_dict(config: AppConfig, include_secrets: bool) -> dict:
    raw = _strip_none(config.model_dump(mode="json"))
    if include_secrets:
        return raw
    for provider in raw.get("providers", {}).values():
        if not isinstance(provider, dict):
            continue
        provider["api_key"] = ""
        provider["oauth_token"] = ""
        provider.pop("api_key_expires_at", None)
    return raw


def _safe_zip_members(zf: ZipFile) -> set[str]:
    safe: set[str] = set()
    for name in zf.namelist():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe archive path: {name!r}")
        safe.add(name)
    return safe


def _toml_loads(text: str) -> dict:
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib.loads(text)
