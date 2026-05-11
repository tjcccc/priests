from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import httpx
import questionary
import typer
from rich.console import Console

from priests.config.loader import is_initialized, save_config
from priests.config.model import (
    AnthropicConfig,
    AppConfig,
    DefaultsConfig,
    OllamaConfig,
    OpenAICompatConfig,
    PathsConfig,
    ProvidersConfig,
    ServiceConfig,
)
from priests.engine_factory import _bootstrap_profiles
from priests.providers.chatgpt_auth import (
    ChatGPTOAuthError,
    authorize_chatgpt_with_browser,
)
from priests.providers.github_copilot_auth import (
    GitHubCopilotAuthError,
    exchange_github_token_for_copilot_token,
    looks_like_copilot_ide_token,
)
from priests.registry import ProviderInfo, get_provider, list_providers

console = Console()
err_console = Console(stderr=True)

_GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_GITHUB_COPILOT_SCOPE = "read:user"


@dataclass(frozen=True)
class ProviderCredentials:
    api_key: str = ""
    custom_base_url: str = ""
    base_url: str = ""
    oauth_token: str = ""
    api_key_expires_at: int | None = None


def _arrow_select(prompt: str, choices: list[questionary.Choice]) -> str:
    """Arrow-key selection. Aborts with a clean message on Ctrl-C."""
    result = questionary.select(prompt, choices=choices, use_shortcuts=False).ask()
    if result is None:
        raise typer.Abort()
    return result


def _fetch_ollama_models(base_url: str) -> list[str] | None:
    """Return sorted model names from Ollama, or None if unreachable."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5.0)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        return None


def _openai_models_url(base_url: str) -> str:
    """Return the /models URL for an OpenAI-compatible provider base URL."""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/models"
    return f"{normalized}/v1/models"


def _fetch_openai_compat_models(base_url: str) -> list[str] | None:
    """Return sorted model IDs from an OpenAI-compatible /v1/models endpoint."""
    try:
        r = httpx.get(_openai_models_url(base_url), timeout=5.0)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []) if "id" in m)
    except Exception:
        return None


def _select_ollama_model(default_url: str) -> tuple[str, str]:
    """Return (model_name, confirmed_base_url). Retries on bad URL."""
    base_url = default_url

    while True:
        console.print(f"[dim]Connecting to Ollama at {base_url} ...[/dim]")
        models = _fetch_ollama_models(base_url)

        if models is None:
            err_console.print(f"[red]Could not connect to Ollama at {base_url}[/red]")
            base_url = typer.prompt("Enter Ollama base URL").strip().rstrip("/")
            continue

        if not models:
            console.print("[yellow]No local models found.[/yellow] Make sure you have pulled at least one model.")
            console.print("  e.g. [bold]ollama pull qwen3:8b[/bold]\n")
            model = typer.prompt("Or enter a model name manually").strip()
            return model, base_url

        model = _arrow_select(
            "Select model:",
            [questionary.Choice(title=m) for m in models],
        )
        return model, base_url


def _select_openai_compat_local_model(provider_label: str, default_url: str) -> tuple[str, str]:
    """Return (model_name, confirmed_base_url) for a no-key local OpenAI-compatible server."""
    base_url = default_url

    while True:
        console.print(f"[dim]Connecting to {provider_label} at {base_url} ...[/dim]")
        models = _fetch_openai_compat_models(base_url)

        if models is None:
            err_console.print(f"[red]Could not connect to {provider_label} at {base_url}[/red]")
            base_url = typer.prompt(f"Enter {provider_label} base URL").strip().rstrip("/")
            continue

        if not models:
            console.print("[yellow]No local models found.[/yellow]")
            model = typer.prompt("Enter a model name manually").strip()
            return model, base_url

        model = _arrow_select(
            "Select model:",
            [questionary.Choice(title=m) for m in models],
        )
        return model, base_url


def _select_model(info: ProviderInfo) -> str:
    """Select a model for a non-Ollama provider.

    Shows an arrow selector when known_models is non-empty, with an
    'Enter manually' escape hatch. Falls back to free-text when the list
    is empty (OpenRouter, Custom).
    """
    if not info.known_models:
        return typer.prompt("Model name").strip()

    _MANUAL = "__manual__"
    choices = [questionary.Choice(title=m) for m in info.known_models]
    choices.append(questionary.Choice(title="Enter manually…", value=_MANUAL))

    selected = _arrow_select("Select model:", choices)
    if selected == _MANUAL:
        return typer.prompt("Model name").strip()
    return selected


def _prompt_secret(label: str) -> str:
    """Prompt for a secret without echoing it in the terminal."""
    return typer.prompt(label, hide_input=True).strip()


async def _start_github_copilot_device_flow() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://github.com/login/device/code",
                headers={"Accept": "application/json", "User-Agent": "priests"},
                data={"client_id": _GITHUB_COPILOT_CLIENT_ID, "scope": _GITHUB_COPILOT_SCOPE},
            )
    except httpx.RequestError as exc:
        raise GitHubCopilotAuthError(
            f"Could not start GitHub device flow: {type(exc).__name__}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise GitHubCopilotAuthError(
            f"GitHub device flow failed: HTTP {response.status_code}: {response.text}"
        )
    return response.json()


async def _poll_github_copilot_device_flow(device_code: str, interval: int, expires_in: int) -> str:
    deadline = time.monotonic() + expires_in
    poll_interval = max(interval, 1)

    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            try:
                response = await client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json", "User-Agent": "priests"},
                    data={
                        "client_id": _GITHUB_COPILOT_CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            except httpx.RequestError as exc:
                raise GitHubCopilotAuthError(
                    f"Could not poll GitHub device flow: {type(exc).__name__}: {exc}"
                ) from exc

            if response.status_code != 200:
                raise GitHubCopilotAuthError(
                    f"GitHub device polling failed: HTTP {response.status_code}: {response.text}"
                )

            data = response.json()
            if token := data.get("access_token"):
                return token

            error = data.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                poll_interval += 5
                continue
            if error == "expired_token":
                raise GitHubCopilotAuthError("The device code expired. Start again.")
            if error == "access_denied":
                raise GitHubCopilotAuthError("Authorization was denied.")
            raise GitHubCopilotAuthError(data.get("error_description") or error or "Authorization failed.")

    raise GitHubCopilotAuthError("The device code expired. Start again.")


async def _github_copilot_device_credentials() -> ProviderCredentials:
    data = await _start_github_copilot_device_flow()
    try:
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        expires_in = int(data.get("expires_in", 900))
        interval = int(data.get("interval", 5))
    except KeyError as exc:
        raise GitHubCopilotAuthError(
            f"GitHub device flow response missing {exc.args[0]!r}"
        ) from exc

    console.print("[bold]GitHub Copilot OAuth[/bold]")
    console.print(f"Open: [bold]{verification_uri}[/bold]")
    console.print(f"Enter code: [bold]{user_code}[/bold]")
    console.print("[dim]Waiting for authorization...[/dim]")

    github_token = await _poll_github_copilot_device_flow(device_code, interval, expires_in)
    copilot = await exchange_github_token_for_copilot_token(github_token)
    return ProviderCredentials(
        api_key=copilot.token,
        base_url=copilot.base_url,
        oauth_token=github_token,
        api_key_expires_at=copilot.expires_at,
    )


def _authorize_github_copilot_device() -> ProviderCredentials:
    try:
        return asyncio.run(_github_copilot_device_credentials())
    except GitHubCopilotAuthError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


def _prompt_github_copilot_credentials(info: ProviderInfo) -> ProviderCredentials:
    method = _arrow_select(
        "GitHub Copilot authorization:",
        [
            questionary.Choice(title="Authorize with GitHub device code (OAuth)", value="device"),
            questionary.Choice(title="Paste token manually", value="manual"),
        ],
    )

    if method == "device":
        return _authorize_github_copilot_device()

    token_type = _arrow_select(
        "Token type:",
        [
            questionary.Choice(title="GitHub OAuth/PAT token (exchange now)", value="github"),
            questionary.Choice(title="Copilot IDE token (starts with tid=)", value="copilot"),
        ],
    )
    token = _prompt_secret("Token")

    if token_type == "copilot":
        if not looks_like_copilot_ide_token(token):
            err_console.print("[yellow]Token does not look like a Copilot IDE token; saving it anyway.[/yellow]")
        return ProviderCredentials(api_key=token, base_url=info.default_base_url)

    try:
        copilot = asyncio.run(exchange_github_token_for_copilot_token(token))
    except GitHubCopilotAuthError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    return ProviderCredentials(
        api_key=copilot.token,
        base_url=copilot.base_url,
        oauth_token=token,
        api_key_expires_at=copilot.expires_at,
    )


def _prompt_chatgpt_credentials(info: ProviderInfo) -> ProviderCredentials:
    method = _arrow_select(
        "ChatGPT credential:",
        [
            questionary.Choice(title="Sign in with ChatGPT in browser (OAuth)", value="oauth"),
            questionary.Choice(title="Paste OpenAI API key", value="api_key"),
        ],
    )
    if method == "oauth":
        try:
            tokens = authorize_chatgpt_with_browser()
        except ChatGPTOAuthError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        return ProviderCredentials(
            api_key=tokens.api_key or tokens.access_token,
            base_url=info.default_base_url,
            oauth_token=tokens.refresh_token,
            api_key_expires_at=tokens.expires_at,
        )

    return ProviderCredentials(api_key=_prompt_secret("OpenAI API key"), base_url=info.default_base_url)


def _prompt_provider_credentials(
    provider_name: str,
    info: ProviderInfo,
    current_custom_base_url: str = "",
) -> ProviderCredentials:
    custom_base_url = ""
    if provider_name == "custom":
        custom_base_url = typer.prompt(
            "Base URL",
            default=current_custom_base_url or "https://",
        ).strip().rstrip("/")

    if info.provider_type == "oauth":
        if provider_name == "github_copilot":
            return _prompt_github_copilot_credentials(info)
        if provider_name == "chatgpt":
            return _prompt_chatgpt_credentials(info)
        return ProviderCredentials(api_key=_prompt_secret("OAuth token"), base_url=info.default_base_url)

    if info.needs_api_key:
        return ProviderCredentials(api_key=_prompt_secret("API key"), custom_base_url=custom_base_url)

    return ProviderCredentials(custom_base_url=custom_base_url)


def _register_model(config: AppConfig, provider: str, model: str) -> None:
    """Add provider/model to config.models.options if not already present."""
    entry = f"{provider}/{model}"
    if entry not in config.models.options:
        config.models.options.append(entry)


def _apply_provider_to_config(
    providers: ProvidersConfig,
    provider: str,
    api_key: str,
    custom_base_url: str,
    *,
    base_url: str = "",
    oauth_token: str = "",
    api_key_expires_at: int | None = None,
) -> None:
    """Write credentials and provider base URL into the providers config in-place."""
    info = get_provider(provider)
    if provider == "anthropic":
        providers.anthropic = AnthropicConfig(api_key=api_key)
    elif provider == "custom":
        providers.custom = OpenAICompatConfig(api_key=api_key, base_url=custom_base_url)
    elif info and info.provider_type == "local":
        current = getattr(providers, provider, None)
        base_url = current.base_url if current else info.default_base_url
        setattr(providers, provider, OllamaConfig(base_url=base_url))
    elif provider != "ollama":
        default_base_url = info.default_base_url if info else ""
        current = getattr(providers, provider, None)
        use_proxy = current.use_proxy if isinstance(current, OpenAICompatConfig) else False
        setattr(
            providers,
            provider,
            OpenAICompatConfig(
                api_key=api_key,
                base_url=base_url or default_base_url,
                use_proxy=use_proxy,
                oauth_token=oauth_token,
                api_key_expires_at=api_key_expires_at,
            ),
        )


def init_command(
    force: Annotated[bool, typer.Option("--force", help="Re-initialize even if already set up.")] = False,
    config_file: Annotated[Path | None, typer.Option("--config", help="Path to priests.toml.")] = None,
) -> None:
    """Initialize priests: configure provider, model, and scaffold profiles."""
    if is_initialized(config_file) and not force:
        console.print("[yellow]priests is already initialized.[/yellow]")
        console.print("Run [bold]priests config show[/bold] to see current settings.")
        console.print("Use [bold]--force[/bold] to re-initialize.")
        raise typer.Exit()

    console.print("[bold]Welcome to priests![/bold]")
    console.print("Let's set up your configuration.\n")

    # --- Provider ---
    providers_list = list_providers()
    provider_name = _arrow_select(
        "Select a provider:",
        [questionary.Choice(title=f"{p.name}  —  {p.label}", value=p.name) for p in providers_list],
    )
    console.print()

    info = next(p for p in providers_list if p.name == provider_name)

    # --- API key + model ---
    local_base_url = ""
    credentials = ProviderCredentials()

    if provider_name == "ollama":
        local_base_url = "http://localhost:11434"
        model, local_base_url = _select_ollama_model(local_base_url)
    elif info.provider_type == "local" and info.known_models is None:
        local_base_url = info.default_base_url
        model, local_base_url = _select_openai_compat_local_model(info.label, local_base_url)
    else:
        credentials = _prompt_provider_credentials(provider_name, info)
        model = _select_model(info)

    console.print()

    # --- Paths ---
    default_profiles_dir = str(Path.home() / ".priests" / "profiles")
    default_sessions_db = str(Path.home() / ".priests" / "sessions.db")

    profiles_dir_str = typer.prompt("Profiles directory", default=default_profiles_dir)
    sessions_db_str = typer.prompt("Sessions database", default=default_sessions_db)

    # --- Build and save config ---
    providers = ProvidersConfig()
    if provider_name == "ollama":
        providers.ollama = OllamaConfig(base_url=local_base_url)
    elif info.provider_type == "local" and local_base_url:
        setattr(providers, provider_name, OllamaConfig(base_url=local_base_url))
    _apply_provider_to_config(
        providers,
        provider_name,
        credentials.api_key,
        credentials.custom_base_url,
        base_url=credentials.base_url,
        oauth_token=credentials.oauth_token,
        api_key_expires_at=credentials.api_key_expires_at,
    )

    config = AppConfig(
        default=DefaultsConfig(provider=provider_name, model=model),
        paths=PathsConfig(
            profiles_dir=Path(profiles_dir_str),
            sessions_db=Path(sessions_db_str),
        ),
        service=ServiceConfig(),
        providers=providers,
    )

    _register_model(config, provider_name, model)
    saved_path = save_config(config, config_file)

    profiles_root = Path(profiles_dir_str).expanduser()
    _bootstrap_profiles(profiles_root)

    console.print(f"\n[green]Initialized![/green] Config saved to {saved_path}")
    console.print(f"  provider : {provider_name}")
    console.print(f"  model    : {model}")
    console.print(f"  profiles : {profiles_root}")
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  [bold]priests run[/bold]                          start an interactive chat")
    console.print("  [bold]priests run --prompt \"...\"[/bold]           send a single prompt")
    console.print("  [bold]priests profile init \"my_profile\"[/bold]    create a custom profile")
    console.print("  [bold]priests model add[/bold]                     configure an additional provider")
    console.print("  [bold]priests --help[/bold]  /  [bold]priests <command> --help[/bold]")
