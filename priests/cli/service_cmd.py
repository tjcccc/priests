from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from priests.config.loader import load_config

service_app = typer.Typer(help="Manage the HTTP service.")
console = Console()
err_console = Console(stderr=True)


@service_app.command("start")
def service_start(
    host: Annotated[str | None, typer.Option("--host", help="Bind host.")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Bind port.")] = None,
    reload: Annotated[bool, typer.Option("--reload", help="Enable auto-reload (dev only).")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Start the FastAPI service (foreground). Press Ctrl-C to stop."""
    import uvicorn

    from priests.service.app import create_app

    config = load_config(config_file)
    if host:
        config.service.host = host
    if port:
        config.service.port = port

    bind_host = config.service.host
    bind_port = config.service.port

    console.print(f"Starting priests service on [bold]http://{bind_host}:{bind_port}[/bold]")
    console.print("[dim]Press Ctrl-C to stop.[/dim]\n")

    uvicorn.run(
        create_app(config),
        host=bind_host,
        port=bind_port,
        reload=reload,
        log_level="info",
    )


@service_app.command("status")
def service_status(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Check if the service is running."""
    import httpx

    config = load_config(config_file)
    bind_host = host or config.service.host
    bind_port = port or config.service.port
    url = f"http://{bind_host}:{bind_port}/health"

    try:
        r = httpx.get(url, timeout=2.0)
        r.raise_for_status()
        data = r.json()
        console.print(f"[green]Running[/green] — {url}")
        console.print(f"  version: {data.get('version', '?')}")
    except httpx.ConnectError:
        console.print(f"[red]Not reachable[/red] — {url}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@service_app.command("stop")
def service_stop() -> None:
    """Stop the service. (Use Ctrl-C in the terminal running 'service start'.)"""
    console.print("[dim]priests service runs in the foreground — use Ctrl-C to stop it.[/dim]")
    console.print("[dim]Background daemon support is planned for a future release.[/dim]")


@service_app.command("logs")
def service_logs(
    follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
) -> None:
    """Show service logs. (Logs go to stdout when running in the foreground.)"""
    console.print("[dim]priests service logs go to stdout/stderr in the foreground.[/dim]")
    console.print("[dim]Run 'priests service start' in a terminal to see live output.[/dim]")
