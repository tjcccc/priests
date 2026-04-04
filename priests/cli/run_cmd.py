from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import anyio
import typer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.markup import escape

from priests.config.loader import load_config
from priests.config.model import AppConfig
from priests.engine_factory import NotInitializedError

run_app = typer.Typer(help="Run a prompt or enter interactive chat.")
console = Console()
err_console = Console(stderr=True)

# Key bindings for interactive chat: Ctrl+J inserts a newline; Enter submits.
_chat_kb = KeyBindings()

@_chat_kb.add("c-j")
def _insert_newline(event):
    event.current_buffer.insert_text("\n")


# Providers that understand the `think` parameter in the request body.
_THINK_PROVIDERS = {"ollama", "bailian", "alibaba_cloud"}


def _build_priest_config(config: AppConfig, provider: str | None, model: str | None, no_think: bool):
    from priest import PriestConfig

    effective_provider = provider or config.default.provider or ""
    if effective_provider in _THINK_PROVIDERS:
        think_on = config.default.think and not no_think
        provider_options: dict = {"think": think_on}
    else:
        provider_options = {}

    return PriestConfig(
        provider=effective_provider,
        model=model or config.default.model,
        timeout_seconds=config.default.timeout_seconds,
        max_output_tokens=config.default.max_output_tokens,
        provider_options=provider_options,
    )


async def _run_single(
    prompt: str,
    config: AppConfig,
    provider: str | None,
    model: str | None,
    profile: str,
    session_id: str | None,
    no_think: bool,
    memories: bool,
) -> None:
    from priest import PriestRequest, SessionRef
    from priests.engine_factory import build_engine, load_global_guide
    from priests.memory.extractor import clean_last_turn, extract_memories, strip_memory_tags, write_memories, trim_memories
    from priests.profile.config import load_profile_config

    engine, store = await build_engine(config)
    priest_config = _build_priest_config(config, provider, model, no_think)

    profile_cfg = load_profile_config(config.paths.profiles_dir, profile)
    memories_on = memories and profile_cfg.memories
    mem_limit = profile_cfg.memories_limit if profile_cfg.memories_limit is not None else config.memory.limit

    guide = load_global_guide(config)
    system_context = ["Running inside priests CLI."]
    if guide:
        system_context = [guide, *system_context]

    session_ref = None
    if session_id:
        session_ref = SessionRef(id=session_id, create_if_missing=True)

    request = PriestRequest(
        config=priest_config,
        profile=profile,
        prompt=prompt,
        session=session_ref,
        system_context=system_context,
    )

    try:
        async with store:
            response = await engine.run(request)
            if response.session:
                await clean_last_turn(store, response.session.id)
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if not response.ok:
        err_console.print(f"[red]Error:[/red] {response.error.code}: {escape(response.error.message)}")
        raise typer.Exit(1)

    facts = extract_memories(response.text or "") if memories_on else []
    if facts:
        memories_dir = config.paths.profiles_dir.expanduser() / profile / "memories"
        write_memories(memories_dir, facts)
        trim_memories(memories_dir, mem_limit)

    console.print(strip_memory_tags(response.text or ""))
    if response.execution.latency_ms is not None:
        console.print(f"[dim]({response.execution.latency_ms}ms)[/dim]")


_CHAT_HELP = """\
[bold]Chat commands:[/bold]
  [bold]/exit[/bold]         Exit the chat.
  [bold]/think on[/bold]     Enable thinking mode (if model supports it).
  [bold]/think off[/bold]    Disable thinking mode.
  [bold]/new[/bold]          Start a new session.
  [bold]/help[/bold]         Show this message.\
"""


async def _run_chat(
    config: AppConfig,
    provider: str | None,
    model: str | None,
    profile: str,
    session_id: str | None,
    no_think: bool,
    memories: bool,
) -> None:
    import uuid

    from priest import PriestConfig, PriestRequest, SessionRef
    from priests.engine_factory import build_engine, load_global_guide
    from priests.memory.extractor import clean_last_turn, extract_memories, strip_memory_tags, write_memories, trim_memories
    from priests.profile.config import load_profile_config

    try:
        engine, store = await build_engine(config)
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    priest_config = _build_priest_config(config, provider, model, no_think)
    think = not no_think and config.default.think

    profile_cfg = load_profile_config(config.paths.profiles_dir, profile)
    memories_on = memories and profile_cfg.memories
    mem_limit = profile_cfg.memories_limit if profile_cfg.memories_limit is not None else config.memory.limit

    guide = load_global_guide(config)
    system_context_base = ["Running inside priests CLI."]
    if guide:
        system_context_base = [guide, *system_context_base]
    memories_dir = config.paths.profiles_dir.expanduser() / profile / "memories"

    sid = session_id or str(uuid.uuid4())
    session_ref = SessionRef(id=sid, create_if_missing=True)

    console.print(f"[dim]Session: {sid}[/dim]")
    console.print("[dim]Type /help for commands, Ctrl-C to quit.[/dim]\n")

    prompt_session: PromptSession[str] = PromptSession(key_bindings=_chat_kb)

    async with store:
        while True:
            try:
                raw = (await prompt_session.prompt_async("user > ")).strip()
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
                    sid = str(uuid.uuid4())
                    session_ref = SessionRef(id=sid, create_if_missing=True)
                    console.print(f"[dim]New session: {sid}[/dim]")
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
                system_context=system_context_base,
            )

            response = await engine.run(request)

            if not response.ok:
                err_console.print(f"[red]Error:[/red] {response.error.code}: {escape(response.error.message)}")
                continue

            await clean_last_turn(store, response.session.id) if response.session else None

            facts = extract_memories(response.text or "") if memories_on else []
            if facts:
                write_memories(memories_dir, facts)
                trim_memories(memories_dir, mem_limit)

            display = strip_memory_tags(response.text or "")
            console.print(f"[bold]{profile} >[/bold] {escape(display)}\n")
            if facts:
                console.print("[dim][memory saved][/dim]")


@run_app.callback(invoke_without_command=True)
def run(
    prompt: Annotated[str | None, typer.Argument(help="Prompt to send. Omit to enter interactive chat.")] = None,
    prompt_opt: Annotated[str | None, typer.Option("--prompt", help="Prompt to send (alternative to positional argument).")] = None,
    provider: Annotated[str | None, typer.Option("--provider", "-p", help="Provider name (e.g. ollama).")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model name.")] = None,
    profile: Annotated[str, typer.Option("--profile", help="Profile name.")] = "default",
    session: Annotated[str | None, typer.Option("--session", "-s", help="Session ID or name.")] = None,
    no_think: Annotated[bool, typer.Option("--no-think", help="Disable model thinking (Qwen3/Ollama).")] = False,
    memories: Annotated[bool, typer.Option("--memories/--no-memories", help="Enable or disable memory loading and saving.")] = True,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Run a single prompt or enter interactive chat (if PROMPT is omitted)."""
    resolved_prompt = prompt or prompt_opt
    config = load_config(config_file)

    if resolved_prompt is None and sys.stdin.isatty():
        anyio.run(_run_chat, config, provider, model, profile, session, no_think, memories)
    elif resolved_prompt is None:
        # Piped input
        resolved_prompt = sys.stdin.read().strip()
        if not resolved_prompt:
            err_console.print("[red]No prompt provided.[/red]")
            raise typer.Exit(1)
        anyio.run(_run_single, resolved_prompt, config, provider, model, profile, session, no_think, memories)
    else:
        anyio.run(_run_single, resolved_prompt, config, provider, model, profile, session, no_think, memories)
