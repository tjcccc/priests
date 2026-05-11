from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from priests.config.model import AppConfig
from priests.registry import REGISTRY


@dataclass
class ProviderStatus:
    name: str
    label: str
    provider_type: str
    configured: bool
    reachable: bool | None
    base_url: str
    model_count: int | None
    models: list[str]
    message: str


@dataclass
class ModelValidation:
    provider: str
    model: str
    valid: bool
    status: str
    message: str


def openai_models_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/models"
    return f"{normalized}/v1/models"


def oauth_models_url(name: str, base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if name == "github_copilot":
        return f"{normalized}/models"
    return openai_models_url(normalized)


def provider_base_url(config: AppConfig, name: str) -> str:
    info = REGISTRY.get(name)
    if info is None:
        return ""
    cfg = getattr(config.providers, name, None)
    if cfg and getattr(cfg, "base_url", ""):
        return cfg.base_url
    return info.default_base_url


def provider_configured(config: AppConfig, name: str) -> bool:
    info = REGISTRY.get(name)
    if info is None:
        return False
    if info.provider_type == "local":
        return bool(provider_base_url(config, name))
    cfg = getattr(config.providers, name, None)
    if name == "custom":
        return bool(cfg and cfg.base_url and cfg.api_key)
    if info.needs_api_key:
        return bool(cfg and (getattr(cfg, "api_key", "") or getattr(cfg, "oauth_token", "")))
    return True


def fetch_ollama_model_records(base_url: str, timeout: float = 5.0) -> list[dict[str, Any]] | None:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        response.raise_for_status()
        return list(response.json().get("models", []))
    except Exception:
        return None


def fetch_ollama_models(base_url: str, timeout: float = 5.0) -> list[str] | None:
    records = fetch_ollama_model_records(base_url, timeout=timeout)
    if records is None:
        return None
    return sorted(str(m["name"]) for m in records if "name" in m)


def fetch_openai_compat_models(base_url: str, timeout: float = 5.0) -> list[str] | None:
    try:
        response = httpx.get(openai_models_url(base_url), timeout=timeout)
        response.raise_for_status()
        return sorted(str(m["id"]) for m in response.json().get("data", []) if "id" in m)
    except Exception:
        return None


def delete_ollama_model(base_url: str, model: str, timeout: float = 30.0) -> None:
    response = httpx.request(
        "DELETE",
        f"{base_url.rstrip('/')}/api/delete",
        json={"name": model},
        timeout=timeout,
    )
    response.raise_for_status()


def provider_status(config: AppConfig, name: str, timeout: float = 2.0) -> ProviderStatus:
    info = REGISTRY[name]
    base_url = provider_base_url(config, name)
    configured = provider_configured(config, name)

    if info.provider_type == "local":
        models = fetch_ollama_models(base_url, timeout) if name == "ollama" else fetch_openai_compat_models(base_url, timeout)
        if models is None:
            return ProviderStatus(
                name=name,
                label=info.label,
                provider_type=info.provider_type,
                configured=configured,
                reachable=False,
                base_url=base_url,
                model_count=None,
                models=[],
                message=f"Could not connect to {base_url}",
            )
        return ProviderStatus(
            name=name,
            label=info.label,
            provider_type=info.provider_type,
            configured=configured,
            reachable=True,
            base_url=base_url,
            model_count=len(models),
            models=models,
            message=f"{len(models)} model(s) available",
        )

    if not configured:
        return ProviderStatus(
            name=name,
            label=info.label,
            provider_type=info.provider_type,
            configured=False,
            reachable=None,
            base_url=base_url,
            model_count=None,
            models=[],
            message="API key or OAuth token is not configured",
        )

    known_models = info.known_models or []
    return ProviderStatus(
        name=name,
        label=info.label,
        provider_type=info.provider_type,
        configured=True,
        reachable=None,
        base_url=base_url,
        model_count=len(known_models) if known_models else None,
        models=known_models,
        message="Configured; live remote health is not checked by default",
    )


async def provider_status_async(config: AppConfig, name: str, timeout: float = 2.0) -> ProviderStatus:
    info = REGISTRY[name]
    base_url = provider_base_url(config, name)
    configured = provider_configured(config, name)

    if info.provider_type != "local":
        return provider_status(config, name, timeout)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if name == "ollama":
                response = await client.get(f"{base_url.rstrip('/')}/api/tags")
                if response.status_code != 200:
                    raise httpx.HTTPStatusError("bad status", request=response.request, response=response)
                models = sorted(str(m["name"]) for m in response.json().get("models", []) if "name" in m)
            else:
                response = await client.get(openai_models_url(base_url))
                if response.status_code != 200:
                    raise httpx.HTTPStatusError("bad status", request=response.request, response=response)
                models = sorted(str(m["id"]) for m in response.json().get("data", []) if "id" in m)
    except Exception:
        return ProviderStatus(
            name=name,
            label=info.label,
            provider_type=info.provider_type,
            configured=configured,
            reachable=False,
            base_url=base_url,
            model_count=None,
            models=[],
            message=f"Could not connect to {base_url}",
        )

    return ProviderStatus(
        name=name,
        label=info.label,
        provider_type=info.provider_type,
        configured=configured,
        reachable=True,
        base_url=base_url,
        model_count=len(models),
        models=models,
        message=f"{len(models)} model(s) available",
    )


def validate_model(config: AppConfig, provider: str, model: str, timeout: float = 5.0) -> ModelValidation:
    info = REGISTRY.get(provider)
    if info is None:
        return ModelValidation(provider, model, False, "error", f"Unknown provider: {provider}")
    if not model:
        return ModelValidation(provider, model, False, "error", "Model name cannot be empty")

    status = provider_status(config, provider, timeout)
    if not status.configured:
        return ModelValidation(provider, model, False, "error", status.message)
    if status.reachable is False:
        return ModelValidation(provider, model, False, "error", status.message)

    if info.provider_type == "local":
        if model in status.models:
            return ModelValidation(provider, model, True, "ok", "Model is available locally")
        return ModelValidation(provider, model, False, "error", f"Model {model!r} was not found at {status.base_url}")

    if info.known_models:
        if model in info.known_models:
            return ModelValidation(provider, model, True, "ok", "Model is in the curated provider list")
        return ModelValidation(
            provider,
            model,
            True,
            "warning",
            "Model is not in the curated list; it may still work if the provider supports it",
        )

    return ModelValidation(provider, model, True, "ok", "Provider accepts manually entered model names")
