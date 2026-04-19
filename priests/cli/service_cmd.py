from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from priests.config.loader import load_config

service_app = typer.Typer(help="Manage the HTTP service.", invoke_without_command=True)
console = Console()
err_console = Console(stderr=True)

_PRIESTS_DIR = Path.home() / ".priests"
_PID_FILE = _PRIESTS_DIR / "service.pid"
_LOG_FILE = _PRIESTS_DIR / "service.log"


# ---------------------------------------------------------------------------
# PID helpers
# ---------------------------------------------------------------------------

def _read_pid() -> int | None:
    try:
        return int(_PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    _PRIESTS_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(pid))


def _clear_pid() -> None:
    _PID_FILE.unlink(missing_ok=True)


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_daemon(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.25)
        if not _is_running(pid):
            break
    else:
        os.kill(pid, signal.SIGKILL)
    _clear_pid()


# ---------------------------------------------------------------------------
# Core start logic (shared by callback, start, and restart)
# ---------------------------------------------------------------------------

def _do_start(
    host: str | None,
    port: int | None,
    daemon: bool,
    config_file: Path | None,
) -> None:
    import uvicorn
    from priests.service.app import create_app

    config = load_config(config_file)
    if host:
        config.service.host = host
    if port:
        config.service.port = port

    bind_host = config.service.host
    bind_port = config.service.port

    if daemon:
        pid = _read_pid()
        if pid and _is_running(pid):
            console.print(
                f"[yellow]Already running[/yellow] (PID {pid}). "
                "Use 'priests service restart' to restart."
            )
            raise typer.Exit(1)

        _PRIESTS_DIR.mkdir(parents=True, exist_ok=True)
        log_fh = open(_LOG_FILE, "a")
        # Spawn a self-contained server process; no CLI re-entry needed.
        script = (
            "import uvicorn\n"
            "from pathlib import Path\n"
            "from priests.config.loader import load_config\n"
            "from priests.service.app import create_app\n"
            f"config = load_config({repr(str(config_file)) if config_file else 'None'})\n"
            f"config.service.host = {repr(bind_host)}\n"
            f"config.service.port = {bind_port}\n"
            f"uvicorn.run(create_app(config), host={repr(bind_host)}, port={bind_port}, log_level='info')\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )
        _write_pid(proc.pid)
        time.sleep(0.6)
        if not _is_running(proc.pid):
            console.print("[red]Failed to start[/red] — check logs:")
            console.print(f"  priests service logs -n 20")
            raise typer.Exit(1)
        console.print(
            f"[green]Started[/green] daemon (PID {proc.pid}) "
            f"on [bold]http://{bind_host}:{bind_port}[/bold]"
        )
        console.print(f"  logs → {_LOG_FILE}")
        return

    # Foreground mode — live output to terminal.
    console.print(f"Starting priests service on [bold]http://{bind_host}:{bind_port}[/bold]")
    console.print("[dim]Press Ctrl-C to stop.[/dim]\n")
    uvicorn.run(create_app(config), host=bind_host, port=bind_port, log_level="info")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@service_app.callback(invoke_without_command=True)
def service_root(
    ctx: typer.Context,
    host: Annotated[str | None, typer.Option("--host", "-h", help="Bind host (default 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option("--port", "-p", help="Bind port (default 8777).")] = None,
    daemon: Annotated[bool, typer.Option("--daemon", "-d", help="Run as background daemon.")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Start the service (foreground by default). Equivalent to 'priests service start'."""
    if ctx.invoked_subcommand is None:
        _do_start(host=host, port=port, daemon=daemon, config_file=config_file)


@service_app.command("start")
def service_start(
    host: Annotated[str | None, typer.Option("--host", "-h", help="Bind host (default 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option("--port", "-p", help="Bind port (default 8777).")] = None,
    daemon: Annotated[bool, typer.Option("--daemon", "-d", help="Run as background daemon.")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Start the service. Foreground by default; add -d for daemon mode."""
    _do_start(host=host, port=port, daemon=daemon, config_file=config_file)


@service_app.command("stop")
def service_stop() -> None:
    """Stop the background daemon."""
    pid = _read_pid()
    if not pid:
        console.print("[dim]No daemon PID file found — nothing to stop.[/dim]")
        raise typer.Exit(1)
    if not _is_running(pid):
        console.print(f"[dim]PID {pid} is not running — clearing stale PID file.[/dim]")
        _clear_pid()
        raise typer.Exit(1)
    _kill_daemon(pid)
    console.print(f"[green]Stopped[/green] (PID {pid})")


@service_app.command("restart")
def service_restart(
    host: Annotated[str | None, typer.Option("--host", "-h", help="Bind host.")] = None,
    port: Annotated[int | None, typer.Option("--port", "-p", help="Bind port.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Restart the background daemon."""
    pid = _read_pid()
    if pid and _is_running(pid):
        _kill_daemon(pid)
        console.print(f"[dim]Stopped PID {pid}.[/dim]")
    _do_start(host=host, port=port, daemon=True, config_file=config_file)


@service_app.command("status")
def service_status(
    host: Annotated[str | None, typer.Option("--host", "-h")] = None,
    port: Annotated[int | None, typer.Option("--port", "-p")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Check if the service is reachable."""
    import httpx

    pid = _read_pid()
    if pid:
        if _is_running(pid):
            console.print(f"[dim]Daemon PID {pid} is alive.[/dim]")
        else:
            console.print(
                f"[yellow]Stale PID file[/yellow] (PID {pid} not found). "
                "Run 'priests service stop' to clean up."
            )

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
    except (httpx.ConnectError, httpx.TimeoutException):
        console.print(f"[red]Not reachable[/red] — {url}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@service_app.command("logs")
def service_logs(
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow log output (like tail -f).")] = False,
    lines: Annotated[int, typer.Option("--lines", "-n", help="Number of lines to show.")] = 50,
) -> None:
    """Show daemon logs."""
    if not _LOG_FILE.exists():
        console.print(f"[dim]No log file at {_LOG_FILE}[/dim]")
        console.print("[dim]Start the daemon with 'priests service start -d' to enable logging.[/dim]")
        raise typer.Exit(1)

    if follow:
        try:
            subprocess.run(["tail", "-f", "-n", str(lines), str(_LOG_FILE)])
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            _python_tail_follow(_LOG_FILE, lines)
    else:
        try:
            subprocess.run(["tail", "-n", str(lines), str(_LOG_FILE)])
        except FileNotFoundError:
            text = _LOG_FILE.read_text()
            console.print("\n".join(text.splitlines()[-lines:]))


def _python_tail_follow(path: Path, initial_lines: int) -> None:
    """Pure-Python fallback for 'tail -f' on systems without tail."""
    text = path.read_text()
    for line in text.splitlines()[-initial_lines:]:
        console.print(line)
    with path.open() as fh:
        fh.seek(0, 2)
        try:
            while True:
                chunk = fh.readline()
                if chunk:
                    console.print(chunk, end="")
                else:
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass
