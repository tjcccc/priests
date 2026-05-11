from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel  # noqa: F401 (used by inline schema classes below)

from priests.config.loader import load_config, save_config
from priests.config.model import AppConfig, OpenAICompatConfig
from priests.engine_factory import build_adapters
from priests.provider_status import provider_status_async, validate_model
from priests.providers.github_copilot_auth import (
    GitHubCopilotAuthError,
    exchange_github_token_for_copilot_token,
)
from priests.registry import REGISTRY
from priests.service.schemas import (
    ConfigPatchRequest,
    ConfigPatchResponse,
    ConfigResponse,
    ProviderStatusOut,
    ProviderConfigOut,
    ProviderRegistryItem,
    ProviderValidateIn,
    ProviderValidateOut,
)

router = APIRouter()

_RESTART_KEYS = frozenset({"service.host", "service.port"})
_GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_GITHUB_COPILOT_SCOPE = "read:user"


class ModelOptionsIn(BaseModel):
    options: list[str]  # each entry: "provider/model"


class ModelOptionsOut(BaseModel):
    options: list[str]


class GitHubCopilotDeviceStartOut(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class GitHubCopilotDevicePollIn(BaseModel):
    device_code: str


class GitHubCopilotDevicePollOut(BaseModel):
    status: str
    message: str = ""
    base_url: str = ""


def _mask(val: str) -> str:
    return "••••••" if val else ""


def _openai_models_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/models"
    return f"{normalized}/v1/models"


def _oauth_models_url(name: str, base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if name == "github_copilot":
        return f"{normalized}/models"
    if normalized.endswith("/v1"):
        return f"{normalized}/models"
    return f"{normalized}/v1/models"


def _config_to_response(config: AppConfig) -> ConfigResponse:
    p = config.providers

    providers: dict[str, ProviderConfigOut] = {}

    # Local no-key providers (OllamaConfig shape)
    for name, info in REGISTRY.items():
        if info.provider_type != "local":
            continue
        cfg = getattr(p, name)
        providers[name] = ProviderConfigOut(base_url=cfg.base_url)

    # Anthropic
    if p.anthropic:
        providers["anthropic"] = ProviderConfigOut(
            api_key=_mask(p.anthropic.api_key),
            use_proxy=p.anthropic.use_proxy,
        )
    else:
        providers["anthropic"] = ProviderConfigOut()

    # OpenAI-compat providers
    compat_providers = [
        "openai", "gemini", "bailian", "alibaba_cloud", "minimax", "deepseek",
        "kimi", "groq", "openrouter", "mistral", "together", "perplexity", "cohere",
        "github_copilot", "chatgpt", "custom",
    ]
    for name in compat_providers:
        cfg = getattr(p, name, None)
        if cfg:
            providers[name] = ProviderConfigOut(
                base_url=cfg.base_url,
                api_key=_mask(cfg.api_key),
                use_proxy=cfg.use_proxy,
            )
        else:
            providers[name] = ProviderConfigOut(
                base_url=REGISTRY[name].default_base_url if name in REGISTRY else "",
            )

    registry = [
        ProviderRegistryItem(
            name=k,
            label=v.label,
            needs_api_key=v.needs_api_key,
            default_base_url=v.default_base_url,
            known_models=v.known_models,
            provider_type=v.provider_type,
        )
        for k, v in REGISTRY.items()
    ]

    paths_raw = config.paths.model_dump(mode="json")
    paths_out = {k: str(v) if v is not None else None for k, v in paths_raw.items()}

    return ConfigResponse(
        defaults=config.default.model_dump(mode="json"),
        providers=providers,
        memory=config.memory.model_dump(mode="json"),
        web_search=config.web_search.model_dump(mode="json"),
        service=config.service.model_dump(mode="json"),
        proxy=config.proxy.model_dump(mode="json") if config.proxy else {"url": ""},
        paths=paths_out,
        registry=registry,
    )


def _set_nested(raw: dict, key: str, value: str) -> None:
    """Set a dotted key in a raw config dict, creating intermediate dicts for None nodes."""
    parts = key.split(".")
    target = raw
    for part in parts[:-1]:
        current = target.get(part)
        if current is None:
            target[part] = {}
        elif not isinstance(current, dict):
            raise ValueError(f"Cannot traverse non-dict node {part!r} in key {key!r}")
        target = target[part]

    leaf = parts[-1]
    existing = target.get(leaf)
    if isinstance(existing, bool):
        target[leaf] = value.lower() in ("true", "1", "yes")
    elif isinstance(existing, int):
        target[leaf] = int(value)
    elif isinstance(existing, float):
        target[leaf] = float(value)
    elif existing is None:
        # No type hint from current value — infer
        if value.lower() in ("true", "false"):
            target[leaf] = value.lower() == "true"
        else:
            try:
                target[leaf] = int(value)
            except ValueError:
                try:
                    target[leaf] = float(value)
                except ValueError:
                    target[leaf] = value
    else:
        target[leaf] = value


@router.get("/config", response_model=ConfigResponse)
async def get_config(request: Request) -> ConfigResponse:
    """Return the current app config with API keys masked."""
    return _config_to_response(request.app.state.config)


@router.patch("/config", response_model=ConfigPatchResponse)
async def patch_config(body: ConfigPatchRequest, request: Request) -> ConfigPatchResponse:
    """Apply partial config updates, hot-reload adapters, return needs_restart flag."""
    needs_restart = any(k in _RESTART_KEYS for k in body.updates)

    try:
        current = load_config()
        raw = current.model_dump(mode="json")

        for key, value in body.updates.items():
            _set_nested(raw, key, value)

        updated = AppConfig.model_validate(raw)
        save_config(updated)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Hot-reload adapters on the live engine (store is unchanged)
    request.app.state.engine._adapters = build_adapters(updated)
    request.app.state.config = updated

    return ConfigPatchResponse(needs_restart=needs_restart)


@router.get("/providers/{name}/models")
async def get_provider_models(name: str, request: Request) -> list[str]:
    """Fetch available models for a provider. For local providers, queries the running server.
    For API/OAuth providers, returns the known_models list from the registry.
    Returns [] on timeout or error.
    """
    info = REGISTRY.get(name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name!r}")

    if info.provider_type == "oauth":
        config: AppConfig = request.app.state.config
        cfg = getattr(config.providers, name, None)
        if cfg and cfg.api_key and cfg.base_url:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    r = await client.get(
                        _oauth_models_url(name, cfg.base_url),
                        headers={"Authorization": f"Bearer {cfg.api_key}"},
                    )
                if r.status_code == 200:
                    data = r.json()
                    models = [m["id"] for m in data.get("data", []) if "id" in m]
                    if models:
                        return sorted(models)
            except Exception:
                pass
        return info.known_models or []

    if info.provider_type != "local":
        return info.known_models or []

    config: AppConfig = request.app.state.config
    p = config.providers

    try:
        if name == "ollama":
            base_url = p.ollama.base_url
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{base_url}/api/tags")
            if r.status_code != 200:
                return []
            data = r.json()
            return [m["name"] for m in data.get("models", [])]
        else:
            cfg = getattr(p, name, None)
            if cfg is None:
                return []
            base_url = cfg.base_url
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(_openai_models_url(base_url))
            if r.status_code != 200:
                return []
            data = r.json()
            return [m["id"] for m in data.get("data", [])]
    except Exception:
        return []

    return []


@router.get("/providers/status", response_model=list[ProviderStatusOut])
async def get_provider_status(request: Request) -> list[ProviderStatusOut]:
    """Return provider configuration and local reachability status."""
    config: AppConfig = request.app.state.config
    rows = []
    for name in REGISTRY:
        status = await provider_status_async(config, name)
        rows.append(
            ProviderStatusOut(
                name=status.name,
                label=status.label,
                provider_type=status.provider_type,
                configured=status.configured,
                reachable=status.reachable,
                base_url=status.base_url,
                model_count=status.model_count,
                message=status.message,
            )
        )
    return rows


@router.post("/providers/validate", response_model=ProviderValidateOut)
async def validate_provider_model(body: ProviderValidateIn, request: Request) -> ProviderValidateOut:
    """Validate a provider/model pair against config, local health, and curated model lists."""
    result = validate_model(request.app.state.config, body.provider, body.model)
    return ProviderValidateOut(
        provider=result.provider,
        model=result.model,
        valid=result.valid,
        status=result.status,
        message=result.message,
    )


@router.post("/providers/github_copilot/device/start", response_model=GitHubCopilotDeviceStartOut)
async def start_github_copilot_device_flow() -> GitHubCopilotDeviceStartOut:
    """Start the GitHub device flow for GitHub Copilot and return the one-time user code."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://github.com/login/device/code",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"client_id": _GITHUB_COPILOT_CLIENT_ID, "scope": _GITHUB_COPILOT_SCOPE},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not start GitHub device flow: {exc}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub device flow failed: HTTP {r.status_code}: {r.text}")

    data = r.json()
    try:
        return GitHubCopilotDeviceStartOut(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            expires_in=int(data.get("expires_in", 900)),
            interval=int(data.get("interval", 5)),
        )
    except KeyError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub device flow response missing {exc.args[0]!r}")


@router.post("/providers/github_copilot/device/poll", response_model=GitHubCopilotDevicePollOut)
async def poll_github_copilot_device_flow(
    body: GitHubCopilotDevicePollIn,
    request: Request,
) -> GitHubCopilotDevicePollOut:
    """Poll GitHub device auth, exchange for a Copilot API token, save config, and hot-reload."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={
                    "client_id": _GITHUB_COPILOT_CLIENT_ID,
                    "device_code": body.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not poll GitHub device flow: {exc}")

    if token_response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub device polling failed: HTTP {token_response.status_code}: {token_response.text}",
        )

    token_data = token_response.json()
    if error := token_data.get("error"):
        if error in {"authorization_pending", "slow_down"}:
            return GitHubCopilotDevicePollOut(status=error)
        if error == "expired_token":
            return GitHubCopilotDevicePollOut(status="expired", message="The device code expired. Start again.")
        if error == "access_denied":
            return GitHubCopilotDevicePollOut(status="denied", message="Authorization was denied.")
        raise HTTPException(status_code=502, detail=token_data.get("error_description") or error)

    github_token = token_data.get("access_token")
    if not github_token:
        raise HTTPException(status_code=502, detail="GitHub device flow did not return an access token.")

    try:
        copilot = await exchange_github_token_for_copilot_token(github_token)
    except GitHubCopilotAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    current = load_config()
    existing = current.providers.github_copilot
    current.providers.github_copilot = OpenAICompatConfig(
        api_key=copilot.token,
        base_url=copilot.base_url,
        use_proxy=existing.use_proxy if existing else False,
        oauth_token=github_token,
        api_key_expires_at=copilot.expires_at,
    )
    save_config(current)
    request.app.state.engine._adapters = build_adapters(current)
    request.app.state.config = current

    return GitHubCopilotDevicePollOut(status="authorized", base_url=copilot.base_url)


@router.put("/config/models/options", response_model=ModelOptionsOut)
async def put_model_options(body: ModelOptionsIn, request: Request) -> ModelOptionsOut:
    """Replace the full configured model options list."""
    for entry in body.options:
        if "/" not in entry:
            raise HTTPException(status_code=422, detail=f"Invalid option format {entry!r}: must be 'provider/model'")
        provider, _model = entry.split("/", 1)
        if provider not in REGISTRY:
            raise HTTPException(status_code=422, detail=f"Unknown provider {provider!r} in option {entry!r}")
    current = load_config()
    current.models.options = body.options
    save_config(current)
    request.app.state.config = current
    return ModelOptionsOut(options=body.options)
