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


def _parse_bool(value: str | None) -> bool | None:
    """Parse a string 'true'/'false' value, returning None if not provided."""
    if value is None:
        return None
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    raise typer.BadParameter(f"Expected true or false, got {value!r}")
console = Console()
err_console = Console(stderr=True)

# Key bindings for interactive chat: Ctrl+J inserts a newline; Enter submits.
_chat_kb = KeyBindings()

@_chat_kb.add("c-j")
def _insert_newline(event):
    event.current_buffer.insert_text("\n")


# Providers that understand the `think` parameter in the request body.
_THINK_PROVIDERS = {"ollama", "bailian", "alibaba_cloud"}


def _build_priest_config(config: AppConfig, provider: str | None, model: str | None, think: bool):
    from priest import PriestConfig

    effective_provider = provider or config.default.provider or ""
    if effective_provider in _THINK_PROVIDERS:
        provider_options: dict = {"think": think}
    else:
        provider_options = {}

    return PriestConfig(
        provider=effective_provider,
        model=model or config.default.model,
        timeout_seconds=config.default.timeout_seconds,
        max_output_tokens=config.default.max_output_tokens,
        provider_options=provider_options,
    )


def _load_mem(path: Path) -> str:
    """Read a memory file, returning empty string if absent."""
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _truncate_auto_short(content: str, max_chars: int) -> str:
    """Return a version of content that fits within max_chars.

    Drops complete ## YYYY-MM-DD sections oldest-first until it fits.
    Never drops the last remaining section — callers must handle the case where
    even the trimmed result exceeds max_chars.
    Falls back to a raw tail-truncation only if no dated sections are found.
    """
    import re

    if len(content) <= max_chars:
        return content
    sections = re.split(r"(?=\n## \d{4}-\d{2}-\d{2})", content)
    if len(sections) <= 1:
        # No dated sections — fall back to keeping the tail
        return content[-max_chars:]
    intro, dated = sections[0], list(sections[1:])
    if len(dated) <= 1:
        # Single dated section — never drop it
        return content
    while len(dated) > 1:
        if len(intro + "".join(dated)) <= max_chars:
            break
        dated.pop(0)  # drop oldest section
    return intro + "".join(dated)


def _build_memory_context(
    memories_dir: Path,
    size_limit: int,
    flat_line_cap: int,
    consolidate: bool,
    context_limit: int = 0,
) -> str:
    """Build the memory system prompt block for a turn.

    On consolidation turns the model receives the full file contents with
    instructions to rewrite them. On all other turns the model still receives
    the loaded contents so it can recall saved facts, plus the append instruction.
    """
    from priests.memory.extractor import USER_FILE, NOTES_FILE, AUTO_FILE

    # Pre-load all three files so we can apply the context cap before building parts.
    user_content  = _load_mem(memories_dir / USER_FILE)
    notes_content = _load_mem(memories_dir / NOTES_FILE)
    auto_content  = _load_mem(memories_dir / AUTO_FILE)

    if context_limit > 0:
        fixed = len(user_content) + len(notes_content)
        available = context_limit - fixed
        if available <= 0:
            auto_content = ""
        else:
            auto_content = _truncate_auto_short(auto_content, available)
            # _truncate_auto_short never drops the last section even if it still
            # exceeds available. Apply a hard tail-truncation as a final safety
            # net so context_limit is always honoured.
            if len(auto_content) > available:
                auto_content = auto_content[-available:]

    parts: list[str] = []

    _mem_guide = (
        "Memory key rules — write from YOUR perspective, third person:\n"
        "  user       = WHO the user is: name, job, background, permanent preferences."
        " Only add a fact here if it will still be true months from now.\n"
        "  notes      = HOW you should behave: tone, language, role constraints."
        " Only add a fact here if it applies to every future session.\n"
        "  auto_short = WHAT is happening now: tasks, reminders, short-lived context."
        " Use this for anything time-sensitive or session-specific."
        " Format as dated sections: ## YYYY-MM-DD\\n\\n- fact\\n- fact\n"
        "When in doubt: if it could expire, it belongs in auto_short, not user."
    )

    if consolidate:
        size_hint = f" Trim auto_short to under {size_limit} characters." if size_limit > 0 else ""
        flat_hint = (
            f" Keep user.md and notes.md under {flat_line_cap} lines each."
            if flat_line_cap > 0
            else " Keep user.md and notes.md concise — remove redundant or outdated entries."
        )
        parts.append(
            f"Your memory files need consolidation. Remove redundant or outdated facts,"
            f" keep each file focused on its purpose, and output the result BEFORE your"
            f" response.{size_hint}{flat_hint}\n\n"
            f"**user.md** (permanent facts about who the user is):\n"
            f"{user_content or '(empty)'}\n\n"
            f"**notes.md** (permanent behavioural constraints for your role):\n"
            f"{notes_content or '(empty)'}\n\n"
            f"**auto_short.md** (time-sensitive tasks, reminders, short-lived context):\n"
            f"{auto_content or '(empty)'}\n\n"
            f"{_mem_guide}\n\n"
            f"Output ONLY the consolidation block. Include ALL three keys — use an empty"
            f" string to clear a file that should be empty after consolidation:\n\n"
            f"<memory_consolidation>\n"
            f'{{\"user\": \"...\", \"notes\": \"...\", \"auto_short\": \"...\"}}\n'
            f"</memory_consolidation>"
        )
    else:
        # On non-consolidation turns, still inject loaded memories so the model
        # can recall saved facts throughout the session.
        if user_content or notes_content or auto_content:
            memo: list[str] = []
            if user_content:
                memo.append(f"**About the user (user.md):**\n{user_content}")
            if notes_content:
                memo.append(f"**Behavioural notes (notes.md):**\n{notes_content}")
            if auto_content:
                memo.append(f"**Recent context (auto_short.md):**\n{auto_content}")
            parts.append("## Loaded Memories\n\n" + "\n\n".join(memo))

    parts.append(
        "If anything from this conversation is worth remembering, output it BEFORE "
        "your response:\n\n"
        "<memory_append>\n"
        "{\"user\": \"...\", \"notes\": \"...\", \"auto_short\": \"...\"}\n"
        "</memory_append>\n\n"
        f"{_mem_guide}\n"
        "Include only keys with new content. Omit the block entirely if nothing is worth saving."
    )

    return "\n\n---\n\n".join(parts)


async def _run_single(
    prompt: str,
    config: AppConfig,
    provider: str | None,
    model: str | None,
    profile: str,
    session_id: str | None,
    think: bool,
    memories: bool,
) -> None:
    import json
    import sys
    from priest import PriestRequest, SessionRef
    from priest.errors import PriestError
    from priests.engine_factory import build_engine, load_global_guide
    from priests.memory.extractor import (
        StreamingStripper, clean_last_turn,
        append_memories, apply_consolidation, trim_memories, needs_consolidation,
        mark_consolidated, deduplicate_file, USER_FILE, NOTES_FILE,
    )
    from priests.profile.config import load_profile_config

    engine, store = await build_engine(config)
    priest_config = _build_priest_config(config, provider, model, think)

    profile_cfg = load_profile_config(config.paths.profiles_dir, profile)
    size_limit = profile_cfg.memories_limit if profile_cfg.memories_limit is not None else config.memory.size_limit
    memories_dir = config.paths.profiles_dir.expanduser() / profile / "memories"

    guide = load_global_guide(config)
    system_context = ["Running inside priests CLI."]
    if guide:
        system_context = [guide, *system_context]
    consolidate = False
    if memories:
        # Dedup runs before needs_consolidation so the sentinel check reflects
        # the post-dedup state. A dedup write would otherwise bump mtime and
        # falsely trigger consolidation on the next session.
        deduplicate_file(memories_dir / USER_FILE)
        deduplicate_file(memories_dir / NOTES_FILE)
        consolidate = needs_consolidation(memories_dir)
        system_context.append(
            _build_memory_context(memories_dir, size_limit, config.memory.flat_line_cap, consolidate, config.memory.context_limit)
        )

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

    start_ms = int(__import__("time").monotonic() * 1000)
    try:
        async with store:
            try:
                stripper = StreamingStripper()
                async for chunk in engine.stream(request):
                    safe = stripper.feed(chunk)
                    if safe:
                        sys.stdout.write(safe)
                        sys.stdout.flush()
                tail = stripper.flush()
                if tail:
                    sys.stdout.write(tail)
                    sys.stdout.flush()
            except PriestError as exc:
                err_console.print(f"\n[red]Error:[/red] {exc.code}: {escape(exc.message)}")
                raise typer.Exit(1)

            if request.session:
                await clean_last_turn(store, request.session.id)
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    latency_ms = int(__import__("time").monotonic() * 1000) - start_ms
    console.print()  # newline after streamed output
    console.print(f"[dim]({latency_ms}ms · {priest_config.provider}/{priest_config.model} · {profile})[/dim]")

    if memories:
        try:
            did_consolidate = False
            if stripper.consolidation_json:
                apply_consolidation(memories_dir, json.loads(stripper.consolidation_json))
                did_consolidate = True
            elif consolidate:
                did_consolidate = True
            if stripper.append_json:
                append_memories(memories_dir, json.loads(stripper.append_json))
            if did_consolidate:
                mark_consolidated(memories_dir)
            trim_memories(memories_dir, size_limit)
        except (json.JSONDecodeError, Exception):
            pass


_CHAT_HELP = """\
[bold]Chat commands:[/bold]
  [bold]/exit[/bold]              Exit the chat.
  [bold]/think on[/bold]          Enable thinking mode (if model supports it).
  [bold]/think off[/bold]         Disable thinking mode.
  [bold]/new[/bold]               Start a new session.
  [bold]/search[/bold] [dim]<query>[/dim]    Run a web search; results are injected into the next message.
  [bold]/remember[/bold] [dim]<text>[/dim]   Save text to today's short memory (auto_short.md).
  [bold]/remember![/bold] [dim]<text>[/dim]  Save text to permanent notes (notes.md).
  [bold]/help[/bold]              Show this message.\
"""


async def _run_chat(
    config: AppConfig,
    provider: str | None,
    model: str | None,
    profile: str,
    session_id: str | None,
    think: bool,
    memories: bool | None,
) -> None:
    import json
    import sys as _sys
    import uuid

    from priest import PriestConfig, PriestRequest, SessionRef
    from priest.errors import PriestError
    from priests.engine_factory import build_engine, load_global_guide
    from priests.memory.extractor import (
        StreamingStripper, clean_last_turn,
        append_memories, apply_consolidation, trim_memories, needs_consolidation,
        mark_consolidated, deduplicate_file, _append_to_file, _append_to_auto_short,
        USER_FILE, NOTES_FILE, AUTO_FILE,
    )
    from priests.profile.config import load_profile_config

    try:
        engine, store = await build_engine(config)
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    priest_config = _build_priest_config(config, provider, model, think)

    profile_cfg = load_profile_config(config.paths.profiles_dir, profile)
    memories_on = memories if memories is not None else profile_cfg.memories
    size_limit = profile_cfg.memories_limit if profile_cfg.memories_limit is not None else config.memory.size_limit
    memories_dir = config.paths.profiles_dir.expanduser() / profile / "memories"

    guide = load_global_guide(config)
    system_context_base = ["Running inside priests CLI."]
    if config.web_search.enabled:
        system_context_base.append(
            "The user has a /search <query> command available to run web searches. "
            "You can ask them to search if current information would help."
        )
    if guide:
        system_context_base = [guide, *system_context_base]

    sid = session_id or str(uuid.uuid4())
    session_ref = SessionRef(id=sid, create_if_missing=True)

    console.print(f"[dim]Model:    {priest_config.provider}/{priest_config.model}[/dim]")
    console.print(f"[dim]Profile:  {profile}[/dim]")
    console.print(f"[dim]Session:  {sid}[/dim]")
    console.print("[dim]Type /help for commands, Ctrl-C to quit.[/dim]\n")

    prompt_session: PromptSession[str] = PromptSession(key_bindings=_chat_kb)

    # Dedup runs before needs_consolidation so the sentinel check reflects the
    # post-dedup state. A dedup write would otherwise bump mtime and falsely
    # trigger consolidation on the next session.
    if memories_on:
        deduplicate_file(memories_dir / USER_FILE)
        deduplicate_file(memories_dir / NOTES_FILE)
    # Consolidation triggers once per session start if memories changed.
    consolidation_needed = memories_on and needs_consolidation(memories_dir)
    consolidation_done = False

    # Pending web search results to inject into the next user message.
    _search_context: str | None = None

    _BOLD = "\033[1m"
    _RESET = "\033[0m"

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
                    # Reset consolidation state so the new session can trigger
                    # consolidation if memory files changed during the previous one.
                    if memories_on:
                        deduplicate_file(memories_dir / USER_FILE)
                        deduplicate_file(memories_dir / NOTES_FILE)
                        consolidation_needed = needs_consolidation(memories_dir)
                    consolidation_done = False
                    console.print(f"[dim]New session: {sid}[/dim]")
                    continue

                elif raw.lower().startswith("/search "):
                    query = raw[len("/search "):].strip()
                    if not query:
                        err_console.print("[yellow]Usage:[/yellow] /search <query>")
                    elif not config.web_search.enabled:
                        err_console.print("[yellow]Web search is disabled in priests.toml.[/yellow]")
                    else:
                        console.print(f"[dim]Searching: {query}…[/dim]")
                        try:
                            from priests.search import search as _do_search
                            _search_context = _do_search(query, config.web_search.max_results)
                            console.print("[dim]Results ready — they will be included in your next message.[/dim]")
                        except RuntimeError as e:
                            err_console.print(f"[red]{escape(str(e))}[/red]")
                        except Exception as e:
                            err_console.print(f"[red]Search failed:[/red] {escape(str(e))}")
                    continue

                elif raw.lower().startswith("/remember! "):
                    content = raw[len("/remember! "):].strip()
                    if not content:
                        err_console.print("[yellow]Usage:[/yellow] /remember! <text>")
                    elif not memories_on:
                        err_console.print("[yellow]Memories are disabled for this profile.[/yellow]")
                    else:
                        _append_to_file(memories_dir / NOTES_FILE, content)
                        console.print("[dim]Saved to notes.md.[/dim]")
                    continue

                elif raw.lower().startswith("/remember "):
                    content = raw[len("/remember "):].strip()
                    if not content:
                        err_console.print("[yellow]Usage:[/yellow] /remember <text>")
                    elif not memories_on:
                        err_console.print("[yellow]Memories are disabled for this profile.[/yellow]")
                    else:
                        _append_to_auto_short(memories_dir / AUTO_FILE, content)
                        console.print("[dim]Saved to auto_short.md.[/dim]")
                    continue

                else:
                    err_console.print(f"[yellow]Unknown command:[/yellow] {raw}  (type /help for available commands)")
                    continue

            # --- Build turn system context ---
            do_consolidate = consolidation_needed and not consolidation_done
            if memories_on:
                turn_context = [*system_context_base, _build_memory_context(
                    memories_dir, size_limit, config.memory.flat_line_cap, do_consolidate, config.memory.context_limit
                )]
            else:
                turn_context = system_context_base

            # --- Normal prompt ---
            extra_context: list[str] = []
            if _search_context:
                extra_context.append(_search_context)
                _search_context = None

            request = PriestRequest(
                config=priest_config,
                profile=profile,
                prompt=raw,
                session=session_ref,
                system_context=turn_context,
                extra_context=extra_context,
            )

            header_printed = False
            stripper = StreamingStripper()
            try:
                async for chunk in engine.stream(request):
                    safe = stripper.feed(chunk)
                    if not header_printed:
                        safe = safe.lstrip("\n")
                    if safe:
                        if not header_printed:
                            _sys.stdout.write(f"{_BOLD}{profile} >{_RESET} ")
                            header_printed = True
                        _sys.stdout.write(safe)
                        _sys.stdout.flush()
                tail = stripper.flush()
                if not header_printed:
                    tail = tail.lstrip("\n")
                if tail:
                    if not header_printed:
                        _sys.stdout.write(f"{_BOLD}{profile} >{_RESET} ")
                        header_printed = True
                    _sys.stdout.write(tail)
                    _sys.stdout.flush()
            except PriestError as exc:
                err_console.print(f"\n[red]Error:[/red] {exc.code}: {escape(exc.message)}")
                continue

            if not header_printed:
                _sys.stdout.write(f"{_BOLD}{profile} >{_RESET}\n")
            _sys.stdout.write("\n\n")
            _sys.stdout.flush()

            if request.session:
                await clean_last_turn(store, request.session.id)

            if memories_on:
                try:
                    if stripper.consolidation_json:
                        apply_consolidation(memories_dir, json.loads(stripper.consolidation_json))
                        consolidation_done = True
                    elif do_consolidate:
                        consolidation_done = True
                    if stripper.append_json:
                        append_memories(memories_dir, json.loads(stripper.append_json))
                    # Touch sentinel AFTER all writes so it's always newer than memory files.
                    # This prevents consolidation from re-triggering every session.
                    if consolidation_done:
                        mark_consolidated(memories_dir)
                    trim_memories(memories_dir, size_limit)
                except (json.JSONDecodeError, Exception):
                    pass


@run_app.callback(invoke_without_command=True)
def run(
    prompt: Annotated[str | None, typer.Argument(help="Prompt to send. Omit to enter interactive chat.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", "-p", help="Provider name (e.g. ollama).")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model name.")] = None,
    profile: Annotated[str | None, typer.Option("--profile", help="Profile name (defaults to the profile set via 'priests profile default').")] = None,
    session: Annotated[str | None, typer.Option("--session", "-s", help="Session ID or name.")] = None,
    think: Annotated[str | None, typer.Option("--think", metavar="BOOL", help="Enable or disable model thinking (true/false). Defaults to value in priests.toml.")] = None,
    memories: Annotated[str | None, typer.Option("--memories", metavar="BOOL", help="Enable or disable memory loading and saving (true/false). Defaults to value in profile.toml.")] = None,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Run a single prompt or enter interactive chat (if PROMPT is omitted)."""
    think_val = _parse_bool(think)
    memories_val = _parse_bool(memories)
    config = load_config(config_file)
    effective_profile: str = profile or config.default.profile
    resolved_think: bool = think_val if think_val is not None else config.default.think

    # One-shot defaults memories to False; chat uses profile default when unset.
    oneshot_memories: bool = memories_val if memories_val is not None else False

    if prompt is None and sys.stdin.isatty():
        anyio.run(_run_chat, config, provider, model, effective_profile, session, resolved_think, memories_val)
    elif prompt is None:
        # Piped input
        prompt = sys.stdin.read().strip()
        if not prompt:
            err_console.print("[red]No prompt provided.[/red]")
            raise typer.Exit(1)
        anyio.run(_run_single, prompt, config, provider, model, effective_profile, session, resolved_think, oneshot_memories)
    else:
        anyio.run(_run_single, prompt, config, provider, model, effective_profile, session, resolved_think, oneshot_memories)
