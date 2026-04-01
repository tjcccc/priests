from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import anyio
import typer
from rich.console import Console
from rich.markup import escape

from priests.config.loader import load_config
from priests.config.model import AppConfig
from priests.engine_factory import NotInitializedError

run_app = typer.Typer(help="Run a prompt or enter interactive chat.")
console = Console()
err_console = Console(stderr=True)


def _build_priest_config(config: AppConfig, provider: str | None, model: str | None, no_think: bool):
    from priest import PriestConfig

    return PriestConfig(
        provider=provider or config.default.provider,
        model=model or config.default.model,
        timeout_seconds=config.default.timeout_seconds,
        max_output_tokens=config.default.max_output_tokens,
        provider_options={"think": False} if (no_think or not config.default.think) else {},
    )


async def _run_single(
    prompt: str,
    config: AppConfig,
    provider: str | None,
    model: str | None,
    profile: str,
    session_id: str | None,
    no_think: bool,
) -> None:
    from priest import PriestRequest, SessionRef
    from priests.engine_factory import build_engine

    engine, store = await build_engine(config)
    priest_config = _build_priest_config(config, provider, model, no_think)

    session_ref = None
    if session_id:
        session_ref = SessionRef(id=session_id, create_if_missing=True)

    request = PriestRequest(
        config=priest_config,
        profile=profile,
        prompt=prompt,
        session=session_ref,
        system_context=["Running inside priests CLI."],
    )

    try:
        async with store:
            response = await engine.run(request)
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if not response.ok:
        err_console.print(f"[red]Error:[/red] {response.error.code}: {response.error.message}")
        raise typer.Exit(1)

    console.print(response.text)
    if response.execution.latency_ms is not None:
        console.print(f"[dim]({response.execution.latency_ms}ms)[/dim]")


_CHAT_HELP = """\
[bold]Chat commands:[/bold]
  [bold]/exit[/bold]         Exit the chat.
  [bold]/think on[/bold]     Enable thinking mode (if model supports it).
  [bold]/think off[/bold]    Disable thinking mode.
  [bold]/new[/bold]          Start a new session. [dim](coming soon)[/dim]
  [bold]/help[/bold]         Show this message.\
"""


async def _run_chat(
    config: AppConfig,
    provider: str | None,
    model: str | None,
    profile: str,
    session_id: str | None,
    no_think: bool,
) -> None:
    import uuid

    from priest import PriestConfig, PriestRequest, SessionRef
    from priests.engine_factory import build_engine

    try:
        engine, store = await build_engine(config)
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    priest_config = _build_priest_config(config, provider, model, no_think)
    think = not no_think and config.default.think

    sid = session_id or str(uuid.uuid4())
    session_ref = SessionRef(id=sid, create_if_missing=True)

    console.print(f"[dim]Session: {sid}[/dim]")
    console.print("[dim]Type /help for commands, Ctrl-C to quit.[/dim]\n")

    async with store:
        while True:
            try:
                raw = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Bye.[/dim]")
                break

            if not raw:
                continue

            # --- Slash commands ---
            if raw.startswith("/"):
                cmd = raw.lower()

                if cmd == "/exit":
                    console.print("[dim]Bye.[/dim]")
                    break

                elif cmd == "/help":
                    console.print(_CHAT_HELP)
                    continue

                elif cmd == "/think on":
                    think = True
                    priest_config = PriestConfig(
                        **{**priest_config.model_dump(), "provider_options": {**priest_config.provider_options, "think": True}}
                    )
                    console.print("[dim]Thinking mode enabled.[/dim]")
                    continue

                elif cmd == "/think off":
                    think = False
                    priest_config = PriestConfig(
                        **{**priest_config.model_dump(), "provider_options": {**priest_config.provider_options, "think": False}}
                    )
                    console.print("[dim]Thinking mode disabled.[/dim]")
                    continue

                elif cmd == "/new":
                    console.print("[dim]/new is coming soon.[/dim]")
                    continue

                else:
                    err_console.print(f"[yellow]Unknown command:[/yellow] {raw}  (type /help for available commands)")
                    continue

            # --- Normal prompt ---
            request = PriestRequest(
                config=priest_config,
                profile=profile,
                prompt=raw,
                session=session_ref,
                system_context=["Running inside priests CLI."],
            )

            response = await engine.run(request)

            if not response.ok:
                err_console.print(f"[red]Error:[/red] {response.error.code}: {response.error.message}")
                continue

            console.print(f"[bold]ai>[/bold] {escape(response.text or '')}\n")


@run_app.callback(invoke_without_command=True)
def run(
    prompt: Annotated[str | None, typer.Argument(help="Prompt to send. Omit to enter interactive chat.")] = None,
    prompt_opt: Annotated[str | None, typer.Option("--prompt", help="Prompt to send (alternative to positional argument).")] = None,
    provider: Annotated[str | None, typer.Option("--provider", "-p", help="Provider name (e.g. ollama).")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model name.")] = None,
    profile: Annotated[str, typer.Option("--profile", help="Profile name.")] = "default",
    session: Annotated[str | None, typer.Option("--session", "-s", help="Session ID or name.")] = None,
    no_think: Annotated[bool, typer.Option("--no-think", help="Disable model thinking (Qwen3/Ollama).")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Run a single prompt or enter interactive chat (if PROMPT is omitted)."""
    resolved_prompt = prompt or prompt_opt
    config = load_config(config_file)

    if resolved_prompt is None and sys.stdin.isatty():
        anyio.run(_run_chat, config, provider, model, profile, session, no_think)
    elif resolved_prompt is None:
        # Piped input
        resolved_prompt = sys.stdin.read().strip()
        if not resolved_prompt:
            err_console.print("[red]No prompt provided.[/red]")
            raise typer.Exit(1)
        anyio.run(_run_single, resolved_prompt, config, provider, model, profile, session, no_think)
    else:
        anyio.run(_run_single, resolved_prompt, config, provider, model, profile, session, no_think)
