from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel  # noqa: F401 (used by inline schema classes below)

from priests.config.loader import load_config, save_config
from priests.config.model import AppConfig
from priests.engine_factory import build_adapters
from priests.registry import REGISTRY
from priests.service.schemas import (
    ConfigPatchRequest,
    ConfigPatchResponse,
    ConfigResponse,
    ProviderConfigOut,
    ProviderRegistryItem,
)

router = APIRouter()

_RESTART_KEYS = frozenset({"service.host", "service.port"})


class ModelOptionsIn(BaseModel):
    options: list[str]  # each entry: "provider/model"


class ModelOptionsOut(BaseModel):
    options: list[str]


def _mask(val: str) -> str:
    return "••••••" if val else ""


def _config_to_response(config: AppConfig) -> ConfigResponse:
    p = config.providers

    providers: dict[str, ProviderConfigOut] = {}

    # Local no-key providers (OllamaConfig shape)
    for name, cfg in [("ollama", p.ollama), ("llamacpp", p.llamacpp), ("lmstudio", p.lmstudio)]:
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
        elif name in ("llamacpp", "lmstudio"):
            base_url = p.llamacpp.base_url if name == "llamacpp" else p.lmstudio.base_url
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{base_url}/v1/models")
            if r.status_code != 200:
                return []
            data = r.json()
            return [m["id"] for m in data.get("data", [])]
    except Exception:
        return []

    return []


@router.put("/config/models/options", response_model=ModelOptionsOut)
async def put_model_options(body: ModelOptionsIn, request: Request) -> ModelOptionsOut:
    """Replace the full configured model options list."""
    for entry in body.options:
        if "/" not in entry:
            raise HTTPException(status_code=422, detail=f"Invalid option format {entry!r}: must be 'provider/model'")
    current = load_config()
    current.models.options = body.options
    save_config(current)
    request.app.state.config = current
    return ModelOptionsOut(options=body.options)
