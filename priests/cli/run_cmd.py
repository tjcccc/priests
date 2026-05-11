from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated

import anyio
import typer
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.markup import escape

from priests.config.loader import load_config, save_config
from priests.config.model import AppConfig, OpenAICompatConfig
from priests.engine_factory import NotInitializedError
from priests.profile.config import resolve_provider_model
from priests.providers.chatgpt_auth import ChatGPTOAuthError, refresh_chatgpt_access_token

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


def _iter_json_payloads(payloads: list[str]):
    import json

    for payload_text in payloads:
        try:
            yield json.loads(payload_text)
        except (json.JSONDecodeError, ValueError):
            continue


console = Console()
err_console = Console(stderr=True)

# Key bindings for interactive chat: Ctrl+J inserts a newline; Enter submits.
_chat_kb = KeyBindings()

@_chat_kb.add("c-j")
def _insert_newline(event):
    event.current_buffer.insert_text("\n")


# Providers that understand the `think` parameter in the request body.
_THINK_PROVIDERS = {"ollama", "bailian", "alibaba_cloud"}


def _build_priest_config(config: AppConfig, provider: str | None, model: str | None, profile: str, think: bool):
    from priest import PriestConfig

    resolved_provider, resolved_model = resolve_provider_model(config, profile, provider, model)
    effective_provider = resolved_provider or ""
    if effective_provider in _THINK_PROVIDERS:
        provider_options: dict = {"think": think}
    else:
        provider_options = {}

    return PriestConfig(
        provider=effective_provider,
        model=resolved_model,
        timeout_seconds=config.default.timeout_seconds,
        max_output_tokens=config.default.max_output_tokens,
        provider_options=provider_options,
    )


def _refresh_chatgpt_config_if_needed(
    config: AppConfig,
    config_file: Path | None,
    provider_name: str | None,
) -> bool:
    if provider_name != "chatgpt":
        return False
    cfg = config.providers.chatgpt
    if not cfg or not cfg.oauth_token:
        return False
    now = int(time.time())
    if cfg.api_key and cfg.api_key_expires_at is not None and cfg.api_key_expires_at > now + 60:
        return False
    try:
        refreshed = refresh_chatgpt_access_token(cfg.oauth_token)
    except ChatGPTOAuthError as exc:
        err_console.print(f"[red]ChatGPT authorization could not be refreshed:[/red] {escape(str(exc))}")
        err_console.print("[yellow]Run priests model add and sign in with ChatGPT again.[/yellow]")
        raise typer.Exit(1)

    config.providers.chatgpt = OpenAICompatConfig.model_validate(
        {
            "api_key": refreshed.api_key or cfg.api_key or refreshed.access_token,
            "base_url": cfg.base_url,
            "use_proxy": cfg.use_proxy,
            "oauth_token": refreshed.refresh_token,
            "api_key_expires_at": refreshed.expires_at,
        }
    )
    save_config(config, config_file)
    return True


def _build_memory_context(
    memories_dir: Path,
    size_limit: int,
    flat_line_cap: int,
    consolidate: bool,
    context_limit: int = 0,
) -> str:
    """Compatibility wrapper for tests and older imports.

    Memory content is now assembled into PriestRequest.memory. This function
    returns only the app-level write/proposal instructions for request.context.
    """
    from priests.memory.extractor import build_memory_instructions

    return build_memory_instructions()


def _model_label(provider: str | None, model: str | None) -> str:
    return f"{provider or 'default'}/{model or 'default'}"


async def _save_cli_turn_meta(config: AppConfig, session_id: str, model: str, elapsed_ms: int) -> None:
    """Persist CLI turn metadata for the Web UI session detail view."""
    try:
        from priests.service.routes.uploads import ensure_uploads_table, save_turn_meta
        db_path = str(config.paths.sessions_db.expanduser())
        await ensure_uploads_table(db_path)
        await save_turn_meta(db_path, session_id, model, elapsed_ms)
    except Exception:
        return


async def _run_single(
    prompt: str,
    config: AppConfig,
    provider: str | None,
    model: str | None,
    profile: str,
    session_id: str | None,
    think: bool,
    memories: bool,
    config_file: Path | None = None,
) -> None:
    import json
    import sys
    from priest import PriestRequest, SessionRef
    from priest.errors import PriestError
    from priests.engine_factory import build_engine, load_global_guide
    from priests.memory.extractor import (
        StreamingStripper, clean_last_turn, pop_last_exchange,
        append_memories, apply_memory_forget, apply_memory_proposals, save_memories,
        save_prompt_memories, trim_memories, forget_prompt_memories,
        deduplicate_file, assemble_memory_entries, build_memory_instructions,
        should_inject_memory_instructions,
        USER_FILE, PREFERENCES_FILE,
    )
    from priests.profile.config import load_profile_config

    resolved_provider, _ = resolve_provider_model(config, profile, provider, model)
    _refresh_chatgpt_config_if_needed(config, config_file, resolved_provider)
    engine, store = await build_engine(config)
    priest_config = _build_priest_config(config, provider, model, profile, think)

    profile_cfg = load_profile_config(config.paths.profiles_dir, profile)
    size_limit = profile_cfg.memories_limit if profile_cfg.memories_limit is not None else config.memory.size_limit
    memories_dir = config.paths.profiles_dir.expanduser() / profile / "memories"

    guide = load_global_guide(config)
    turn_context = ["Running inside priests CLI."]
    if guide:
        turn_context = [guide, *turn_context]
    if memories:
        deduplicate_file(memories_dir / USER_FILE)
        deduplicate_file(memories_dir / PREFERENCES_FILE)
        if should_inject_memory_instructions(prompt):
            turn_context.append(build_memory_instructions())

    session_ref = None
    if session_id:
        session_ref = SessionRef(id=session_id, create_if_missing=True)

    request = PriestRequest(
        config=priest_config,
        profile=profile,
        prompt=prompt,
        session=session_ref,
        context=turn_context,
        memory=(
            assemble_memory_entries(memories_dir, config.memory.context_limit, thinking=think, prompt=prompt)
            if memories
            else []
        ),
    )

    start_ms = int(__import__("time").monotonic() * 1000)
    latency_ms: int | None = None
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
                latency_ms = int(__import__("time").monotonic() * 1000) - start_ms
                await _save_cli_turn_meta(
                    config,
                    request.session.id,
                    _model_label(priest_config.provider, priest_config.model),
                    latency_ms,
                )
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if latency_ms is None:
        latency_ms = int(__import__("time").monotonic() * 1000) - start_ms
    console.print()  # newline after streamed output
    console.print(f"[dim]({latency_ms}ms · {priest_config.provider}/{priest_config.model} · {profile})[/dim]")

    if memories:
        try:
            for payload in _iter_json_payloads(stripper.save_jsons):
                save_memories(memories_dir, payload, session_id=session_id)
            for payload in _iter_json_payloads(stripper.append_jsons):
                append_memories(memories_dir, payload, session_id=session_id)
            for payload in _iter_json_payloads(stripper.proposal_jsons):
                apply_memory_proposals(memories_dir, payload, session_id=session_id)
            for payload in _iter_json_payloads(stripper.forget_jsons):
                apply_memory_forget(memories_dir, payload, session_id=session_id)
            forget_prompt_memories(memories_dir, prompt, session_id=session_id)
            save_prompt_memories(memories_dir, prompt, session_id=session_id)
            trim_memories(memories_dir, size_limit)
        except (json.JSONDecodeError, Exception):
            pass


def _read_clipboard_image() -> str | None:
    """Save macOS clipboard image as PNG to a temp file. Returns path or None."""
    try:
        check = subprocess.run(
            ["osascript", "-e", "clipboard info"],
            capture_output=True, text=True, timeout=2,
        )
        # Screenshots appear as «class PNGf»; Finder copies may show "picture".
        stdout = check.stdout.lower()
        if not any(x in stdout for x in ("picture", "pngf", "tiff", "jpeg", "image")):
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        script = (
            f'set imageData to (the clipboard as «class PNGf»)\n'
            f'set fileRef to open for access POSIX file "{tmp.name}" with write permission\n'
            f'write imageData to fileRef\n'
            f'close access fileRef'
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        if result.returncode != 0 or os.path.getsize(tmp.name) == 0:
            os.unlink(tmp.name)
            return None
        return tmp.name
    except Exception:
        return None


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}


def _extract_image_paths(text: str, pending: list) -> str:
    """Replace image file paths/names in text with [image #N] markers.

    Scans for tokens that look like image file paths (absolute, ~/..., or bare
    filenames with a known image extension). For each one found that resolves to
    a real file, appends it to pending and replaces the token in text.
    Returns the rewritten text.
    """
    import re
    import shlex

    # Match: absolute paths, home-relative paths, or bare filenames with image ext
    pattern = re.compile(
        r'(?<!\[)'                          # not already inside [...]
        r'((?:~|/)[^\s,;"\']*|[^\s/,;"\']+)'  # path-like token
        r'(?!\])'
    )

    def _replace(m: re.Match) -> str:
        token = m.group(1)
        # Unescape shell quoting if needed
        try:
            token = shlex.split(token)[0]
        except ValueError:
            pass
        suffix = Path(token).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            return m.group(0)
        # Resolve home-relative and check existence
        resolved = Path(token).expanduser()
        if not resolved.exists():
            # Try treating bare filename as relative to Downloads
            candidate = Path.home() / "Downloads" / token
            if candidate.exists():
                resolved = candidate
            else:
                return m.group(0)
        pending.append(str(resolved))
        return f"[image #{len(pending)}]"

    return pattern.sub(_replace, text)


_CHAT_HELP = """\
[bold]Chat commands:[/bold]
  [bold]/exit[/bold]              Exit the chat.
  [bold]/think on[/bold]          Enable thinking mode (if model supports it).
  [bold]/think off[/bold]         Disable thinking mode.
  [bold]/new[/bold]               Start a new session.
  [bold]Cmd+V[/bold]              Paste image from clipboard as [image #N] (macOS). Requires a vision model.
  [bold]/image[/bold]             Attach image from clipboard via /image command.
  [bold]/image[/bold] [dim]<path>[/dim]      Attach image from file path.
  [bold]/image clear[/bold]       Remove all pending images.
  [dim]Tip: pasting an image file path (Cmd+V) also auto-attaches it.[/dim]
  [bold]/search[/bold] [dim]<query>[/dim]    Run a web search; results are injected into the next message.
  [bold]/remember[/bold] [dim]<text>[/dim]       Save text to short-term memory (auto_short.jsonl).
  [bold]/remember user[/bold] [dim]<text>[/dim]  Save approved durable user memory (user.jsonl).
  [bold]/remember pref[/bold] [dim]<text>[/dim]  Save approved durable preference memory (preferences.jsonl).
  [bold]/forget[/bold] [dim]<query>[/dim]        Soft-forget matching active memory by superseding it.
  [bold]/delete-memory[/bold] [dim]<query>[/dim] Permanently remove matching JSONL memory records.
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
    config_file: Path | None = None,
) -> None:
    import json
    import sys as _sys
    import uuid

    from priest import ImageInput, PriestConfig, PriestRequest, SessionRef
    from priest.errors import PriestError
    from priests.engine_factory import build_adapters, build_engine, load_global_guide
    from priests.memory.extractor import (
        StreamingStripper, clean_last_turn, pop_last_exchange,
        append_memories, apply_memory_forget, apply_memory_proposals, save_memories,
        save_prompt_memories, trim_memories, forget_memories, forget_prompt_memories,
        deduplicate_file, assemble_memory_entries, build_memory_instructions,
        remember_short, remember_user, remember_preference,
        delete_memories, should_inject_memory_instructions,
        USER_FILE, PREFERENCES_FILE,
    )
    from priests.profile.config import load_profile_config

    resolved_provider, _ = resolve_provider_model(config, profile, provider, model)
    _refresh_chatgpt_config_if_needed(config, config_file, resolved_provider)

    try:
        engine, store = await build_engine(config)
    except NotInitializedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    priest_config = _build_priest_config(config, provider, model, profile, think)

    profile_cfg = load_profile_config(config.paths.profiles_dir, profile)
    memories_on = memories if memories is not None else profile_cfg.memories
    size_limit = profile_cfg.memories_limit if profile_cfg.memories_limit is not None else config.memory.size_limit
    memories_dir = config.paths.profiles_dir.expanduser() / profile / "memories"

    guide = load_global_guide(config)
    context_base = ["Running inside priests CLI."]
    tool_hints: list[str] = []
    if config.web_search.enabled:
        tool_hints.append(
            "Web search: emit <search_query>your query</search_query> and nothing else. "
            "The system runs the search and re-prompts you with results. "
            "Do NOT narrate or simulate a search."
        )
    tool_hints.append(
        "File reading: when the user asks you to read a local file, emit "
        "<read_file>/absolute/path/to/file</read_file> and nothing else. "
        "The system reads the file and re-prompts you with its contents."
    )
    context_base.append(
        "You have the following tools available — use them by emitting the tag alone with no other text:\n"
        + "\n".join(f"- {h}" for h in tool_hints)
    )
    if guide:
        context_base = [guide, *context_base]

    sid = session_id or str(uuid.uuid4())
    session_ref = SessionRef(id=sid, create_if_missing=True)

    console.print(f"[dim]Model:    {priest_config.provider}/{priest_config.model}[/dim]")
    console.print(f"[dim]Profile:  {profile}[/dim]")
    console.print(f"[dim]Session:  {sid}[/dim]")
    console.print("[dim]Type /help for commands, Ctrl-C to quit.[/dim]\n")

    # Pending images to attach to the next user message.
    _pending_images: list = []

    # Local bindings close over _pending_images so Ctrl+V can append to it.
    _local_kb = KeyBindings()

    @_local_kb.add("<bracketed-paste>")
    def _paste_or_image(event) -> None:
        import re
        data = event.data.replace("\r\n", "\n").replace("\r", "\n")

        # Try the entire paste as a single file path (handles spaces and backslash-
        # escaped spaces that Terminal inserts when you drag a file, e.g.
        # "/Users/foo/Desktop/Screenshot\ 2026-04-18\ at\ 20.42.46.png").
        stripped = data.strip()
        if stripped:
            import shlex as _shlex
            try:
                unescaped = _shlex.split(stripped)[0]
            except (ValueError, IndexError):
                unescaped = stripped
            candidate = Path(unescaped).expanduser()
            if candidate.suffix.lower() in _IMAGE_EXTENSIONS and candidate.exists():
                _pending_images.append(str(candidate))
                event.current_buffer.insert_text(f"[image #{len(_pending_images)}]")
                return

        # Embedded image paths without spaces (absolute or ~-relative).
        _ext_pat = "|".join(e.lstrip(".") for e in _IMAGE_EXTENSIONS)
        if re.search(rf'(?:~|/)[^\s]*\.(?:{_ext_pat})(?:\s|$)', data, re.IGNORECASE):
            event.current_buffer.insert_text(_extract_image_paths(data, _pending_images))
            return

        # No path detected — try to read an image that was copied directly to clipboard
        # (e.g. Cmd+Shift+Control+4 captures screenshot straight to clipboard).
        path = _read_clipboard_image()
        if path:
            _pending_images.append(path)
            event.current_buffer.insert_text(f"[image #{len(_pending_images)}]")
        else:
            event.current_buffer.insert_text(data)

    prompt_session: PromptSession[str] = PromptSession(
        key_bindings=merge_key_bindings([_chat_kb, _local_kb])
    )

    # Keep approved durable memory tidy without giving the model rewrite access.
    if memories_on:
        deduplicate_file(memories_dir / USER_FILE)
        deduplicate_file(memories_dir / PREFERENCES_FILE)

    # Pending web search results to inject into the next user message.
    _search_context: str | None = None

    _BOLD = "\033[1m"
    _RESET = "\033[0m"

    async with store:
        while True:
            try:
                img_label = f" [img:{len(_pending_images)}]" if _pending_images else ""
                raw = (await prompt_session.prompt_async(f"user{img_label} > ")).strip()
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
                    if memories_on:
                        deduplicate_file(memories_dir / USER_FILE)
                        deduplicate_file(memories_dir / PREFERENCES_FILE)
                    console.print(f"[dim]New session: {sid}[/dim]")
                    continue

                elif cmd == "/image":
                    path = _read_clipboard_image()
                    if path is None:
                        err_console.print("[yellow]No image found in clipboard (or not on macOS).[/yellow]")
                    else:
                        _pending_images.append(path)
                        console.print(f"[dim]Image {len(_pending_images)} attached from clipboard.[/dim]")
                    continue

                elif cmd == "/image clear":
                    for p in _pending_images:
                        try:
                            os.unlink(p)
                        except OSError:
                            pass
                    _pending_images.clear()
                    console.print("[dim]Pending images cleared.[/dim]")
                    continue

                elif raw.lower().startswith("/image "):
                    img_path = raw[len("/image "):].strip()
                    if not img_path:
                        err_console.print("[yellow]Usage:[/yellow] /image <path>")
                    elif not Path(img_path).exists():
                        err_console.print(f"[red]File not found:[/red] {escape(img_path)}")
                    else:
                        _pending_images.append(img_path)
                        console.print(f"[dim]Image {len(_pending_images)} attached: {img_path}[/dim]")
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
                            from priests.search import format_search_context, search as _do_search
                            _search_context = format_search_context(_do_search(query, config.web_search.max_results))
                            console.print("[dim]Results ready — they will be included in your next message.[/dim]")
                        except RuntimeError as e:
                            err_console.print(f"[red]{escape(str(e))}[/red]")
                        except Exception as e:
                            err_console.print(f"[red]Search failed:[/red] {escape(str(e))}")
                    continue

                elif raw.lower().startswith("/remember! "):
                    err_console.print(
                        "[yellow]/remember! no longer writes notes.md.[/yellow] "
                        "Use /remember user <text> or /remember pref <text> for approved durable memory."
                    )
                    continue

                elif raw.lower().startswith("/forget "):
                    query = raw[len("/forget "):].strip()
                    if not query:
                        err_console.print("[yellow]Usage:[/yellow] /forget <query>")
                    elif not memories_on:
                        err_console.print("[yellow]Memories are disabled for this profile.[/yellow]")
                    else:
                        count = forget_memories(memories_dir, query)
                        console.print(
                            f"[dim]Soft-forgot {count} matching memory entr{'y' if count == 1 else 'ies'} "
                            "by marking them superseded.[/dim]"
                        )
                    continue

                elif raw.lower().startswith("/delete-memory "):
                    query = raw[len("/delete-memory "):].strip()
                    if not query:
                        err_console.print("[yellow]Usage:[/yellow] /delete-memory <query>")
                    elif not memories_on:
                        err_console.print("[yellow]Memories are disabled for this profile.[/yellow]")
                    else:
                        count = delete_memories(memories_dir, query)
                        console.print(
                            f"[dim]Permanently deleted {count} matching JSONL memory "
                            f"record{'s' if count != 1 else ''}.[/dim]"
                        )
                    continue

                elif raw.lower().startswith("/remember user "):
                    content = raw[len("/remember user "):].strip()
                    if not content:
                        err_console.print("[yellow]Usage:[/yellow] /remember user <text>")
                    elif not memories_on:
                        err_console.print("[yellow]Memories are disabled for this profile.[/yellow]")
                    else:
                        remember_user(memories_dir, content)
                        console.print("[dim]Saved to user.jsonl.[/dim]")
                    continue

                elif raw.lower().startswith("/remember pref "):
                    content = raw[len("/remember pref "):].strip()
                    if not content:
                        err_console.print("[yellow]Usage:[/yellow] /remember pref <text>")
                    elif not memories_on:
                        err_console.print("[yellow]Memories are disabled for this profile.[/yellow]")
                    else:
                        remember_preference(memories_dir, content)
                        console.print("[dim]Saved to preferences.jsonl.[/dim]")
                    continue

                elif raw.lower().startswith("/remember "):
                    content = raw[len("/remember "):].strip()
                    if not content:
                        err_console.print("[yellow]Usage:[/yellow] /remember <text>")
                    elif not memories_on:
                        err_console.print("[yellow]Memories are disabled for this profile.[/yellow]")
                    else:
                        remember_short(memories_dir, content)
                        console.print("[dim]Saved to auto_short.jsonl.[/dim]")
                    continue

                else:
                    err_console.print(f"[yellow]Unknown command:[/yellow] {raw}  (type /help for available commands)")
                    continue

            # --- Build turn system context ---
            if memories_on:
                turn_context = [*context_base]
                if should_inject_memory_instructions(raw):
                    turn_context.append(build_memory_instructions())
                turn_memory = assemble_memory_entries(
                    memories_dir,
                    config.memory.context_limit,
                    thinking=think,
                    prompt=raw,
                )
            else:
                turn_context = context_base
                turn_memory = []

            # --- Normal prompt ---
            # Auto-detect image file paths pasted via Cmd+V and replace with markers.
            before_count = len(_pending_images)
            raw = _extract_image_paths(raw, _pending_images)
            for _i, _p in enumerate(_pending_images[before_count:], start=before_count + 1):
                console.print(f"[dim]Auto-attached image {_i}: {_p}[/dim]")

            user_context: list[str] = []
            if _search_context:
                user_context.append(_search_context)
                _search_context = None

            images = [ImageInput(path=p) for p in _pending_images]

            if _refresh_chatgpt_config_if_needed(config, config_file, priest_config.provider):
                engine._adapters = build_adapters(config)

            request = PriestRequest(
                config=priest_config,
                profile=profile,
                prompt=raw,
                session=session_ref,
                context=turn_context,
                memory=turn_memory,
                user_context=user_context,
                images=images,
            )

            turn_start_ms = int(time.monotonic() * 1000)
            header_printed = False
            visible_parts: list[str] = []
            stripper = StreamingStripper()
            try:
                async for chunk in engine.stream(request):
                    safe = stripper.feed(chunk)
                    if not header_printed:
                        safe = safe.lstrip("\n")
                    if safe:
                        visible_parts.append(safe)
                        if not header_printed:
                            _sys.stdout.write(f"{_BOLD}{profile} >{_RESET} ")
                            header_printed = True
                        _sys.stdout.write(safe)
                        _sys.stdout.flush()
                tail = stripper.flush()
                if not header_printed:
                    tail = tail.lstrip("\n")
                if tail:
                    visible_parts.append(tail)
                    if not header_printed:
                        _sys.stdout.write(f"{_BOLD}{profile} >{_RESET} ")
                        header_printed = True
                    _sys.stdout.write(tail)
                    _sys.stdout.flush()
            except PriestError as exc:
                err_console.print(f"\n[red]Error:[/red] {exc.code}: {escape(exc.message)}")
                continue

            # --- Agentic tool loops ---
            # When the model emits only a tool tag (nothing visible), run the
            # tool and re-stream with the result injected as user_context.

            async def _agentic_rerun(tool_context: str) -> tuple[StreamingStripper, bool]:
                """Pop probe exchange, re-run with tool_context, return new stripper + header_printed."""
                nonlocal request
                if request.session:
                    await pop_last_exchange(store, request.session.id)
                tool_request = PriestRequest(
                    config=priest_config,
                    profile=profile,
                    prompt=raw,
                    session=session_ref,
                    context=turn_context,
                    memory=turn_memory,
                    user_context=[tool_context],
                    images=images,
                )
                new_stripper = StreamingStripper()
                hp = False
                try:
                    async for chunk in engine.stream(tool_request):
                        safe = new_stripper.feed(chunk)
                        if not hp:
                            safe = safe.lstrip("\n")
                        if safe:
                            if not hp:
                                _sys.stdout.write(f"{_BOLD}{profile} >{_RESET} ")
                                hp = True
                            _sys.stdout.write(safe)
                            _sys.stdout.flush()
                    tail = new_stripper.flush()
                    if not hp:
                        tail = tail.lstrip("\n")
                    if tail:
                        if not hp:
                            _sys.stdout.write(f"{_BOLD}{profile} >{_RESET} ")
                            hp = True
                        _sys.stdout.write(tail)
                        _sys.stdout.flush()
                except PriestError as exc:
                    err_console.print(f"\n[red]Error:[/red] {exc.code}: {escape(exc.message)}")
                request = tool_request
                return new_stripper, hp

            ran_web_search = False
            fallback_search = False
            if config.web_search.enabled and stripper.search_query and not header_printed:
                query = stripper.search_query.strip()
            elif config.web_search.enabled:
                from priests.search import should_fallback_to_search
                fallback_search = should_fallback_to_search(raw, "".join(visible_parts))
                query = raw if fallback_search else ""
            else:
                query = ""

            if query:
                if fallback_search and header_printed:
                    _sys.stdout.write("\n")
                    _sys.stdout.flush()
                console.print(f"[dim]Searching: {query}…[/dim]")
                try:
                    from priests.search import format_search_context, search as _do_search
                    tool_ctx = format_search_context(_do_search(query, config.web_search.max_results))
                except Exception as _se:
                    tool_ctx = f"Search failed: {_se}. Answer the user by explaining that web search failed."
                ran_web_search = True
                stripper, header_printed = await _agentic_rerun(tool_ctx)

            elif stripper.read_file_path and not header_printed:
                _FILE_SIZE_LIMIT = 100_000
                fpath = stripper.read_file_path.strip()
                console.print(f"[dim]Reading: {fpath}…[/dim]")
                try:
                    raw_bytes = Path(fpath).read_bytes()
                    text = raw_bytes.decode("utf-8", errors="replace")
                    if len(text) > _FILE_SIZE_LIMIT:
                        text = text[:_FILE_SIZE_LIMIT] + f"\n\n[truncated — file exceeds {_FILE_SIZE_LIMIT} chars]"
                    tool_ctx = f"## File: {fpath}\n\n{text}"
                except FileNotFoundError:
                    tool_ctx = f"File not found: {fpath}"
                except PermissionError:
                    tool_ctx = f"Permission denied reading: {fpath}"
                except Exception as _fe:
                    tool_ctx = f"Error reading {fpath}: {_fe}"
                stripper, header_printed = await _agentic_rerun(tool_ctx)

            if not header_printed:
                if ran_web_search:
                    fallback = "Web search completed, but the model returned no visible answer."
                    if stripper.search_query:
                        fallback = "Web search completed, but the model requested another search instead of answering."
                    _sys.stdout.write(f"{_BOLD}{profile} >{_RESET} {fallback}")
                else:
                    _sys.stdout.write(f"{_BOLD}{profile} >{_RESET}\n")
            _sys.stdout.write("\n\n")
            _sys.stdout.flush()

            # Clean up temp clipboard images and clear list after successful turn.
            for _img_path in _pending_images:
                try:
                    if _img_path.startswith(tempfile.gettempdir()):
                        os.unlink(_img_path)
                except OSError:
                    pass
            _pending_images.clear()

            if request.session:
                await clean_last_turn(store, request.session.id)
                elapsed_ms = int(time.monotonic() * 1000) - turn_start_ms
                await _save_cli_turn_meta(
                    config,
                    request.session.id,
                    _model_label(priest_config.provider, priest_config.model),
                    elapsed_ms,
                )

            if memories_on:
                try:
                    for payload in _iter_json_payloads(stripper.save_jsons):
                        save_memories(memories_dir, payload, session_id=sid)
                    for payload in _iter_json_payloads(stripper.append_jsons):
                        append_memories(memories_dir, payload, session_id=sid)
                    for payload in _iter_json_payloads(stripper.proposal_jsons):
                        apply_memory_proposals(memories_dir, payload, session_id=sid)
                    for payload in _iter_json_payloads(stripper.forget_jsons):
                        apply_memory_forget(memories_dir, payload, session_id=sid)
                    forget_prompt_memories(memories_dir, raw, session_id=sid)
                    save_prompt_memories(memories_dir, raw, session_id=sid)
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
        anyio.run(_run_chat, config, provider, model, effective_profile, session, resolved_think, memories_val, config_file)
    elif prompt is None:
        # Piped input
        prompt = sys.stdin.read().strip()
        if not prompt:
            err_console.print("[red]No prompt provided.[/red]")
            raise typer.Exit(1)
        anyio.run(_run_single, prompt, config, provider, model, effective_profile, session, resolved_think, oneshot_memories, config_file)
    else:
        anyio.run(_run_single, prompt, config, provider, model, effective_profile, session, resolved_think, oneshot_memories, config_file)
