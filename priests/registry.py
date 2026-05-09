from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ProviderInfo:
    name: str             # key used in config and adapters
    label: str            # human-readable display name
    needs_api_key: bool
    default_base_url: str
    # None  → fetch dynamically at runtime (Ollama, llama.cpp, LM Studio)
    # []    → no curated list; user enters model name as free text
    # [..] → curated list shown in select
    known_models: list[str] | None = field(default_factory=list)
    provider_type: Literal["local", "api", "oauth"] = "api"


REGISTRY: dict[str, ProviderInfo] = {
    # ── Local ──────────────────────────────────────────────────────────────
    "ollama": ProviderInfo(
        name="ollama",
        label="Ollama (local)",
        needs_api_key=False,
        default_base_url="http://localhost:11434",
        known_models=None,  # fetched dynamically from /api/tags
        provider_type="local",
    ),
    "llamacpp": ProviderInfo(
        name="llamacpp",
        label="llama.cpp (local)",
        needs_api_key=False,
        default_base_url="http://localhost:8080",
        known_models=None,  # fetched dynamically from /v1/models
        provider_type="local",
    ),
    "lmstudio": ProviderInfo(
        name="lmstudio",
        label="LM Studio (local)",
        needs_api_key=False,
        default_base_url="http://localhost:1234/v1",
        known_models=None,  # fetched dynamically from /v1/models
        provider_type="local",
    ),
    "rapidmlx": ProviderInfo(
        name="rapidmlx",
        label="Rapid-MLX (local)",
        needs_api_key=False,
        default_base_url="http://localhost:8000/v1",
        known_models=None,  # fetched dynamically from /v1/models
        provider_type="local",
    ),
    # ── API ────────────────────────────────────────────────────────────────
    "openai": ProviderInfo(
        name="openai",
        label="OpenAI",
        needs_api_key=True,
        default_base_url="https://api.openai.com/v1",
        known_models=[
            "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
            "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "gpt-4o", "gpt-4o-mini",
            "o4-mini", "o3", "o3-mini",
        ],
        provider_type="api",
    ),
    "anthropic": ProviderInfo(
        name="anthropic",
        label="Anthropic Claude",
        needs_api_key=True,
        default_base_url="https://api.anthropic.com",
        known_models=[
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        provider_type="api",
    ),
    "gemini": ProviderInfo(
        name="gemini",
        label="Google Gemini",
        needs_api_key=True,
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        known_models=[
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ],
        provider_type="api",
    ),
    "deepseek": ProviderInfo(
        name="deepseek",
        label="DeepSeek",
        needs_api_key=True,
        default_base_url="https://api.deepseek.com/v1",
        known_models=[
            "deepseek-chat",
            "deepseek-reasoner",
        ],
        provider_type="api",
    ),
    "mistral": ProviderInfo(
        name="mistral",
        label="Mistral AI",
        needs_api_key=True,
        default_base_url="https://api.mistral.ai/v1",
        known_models=[
            "mistral-large-latest",
            "mistral-medium-latest",
            "mistral-small-latest",
            "codestral-latest",
        ],
        provider_type="api",
    ),
    "groq": ProviderInfo(
        name="groq",
        label="Groq",
        needs_api_key=True,
        default_base_url="https://api.groq.com/openai/v1",
        known_models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "qwen/qwen3-32b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        ],
        provider_type="api",
    ),
    "perplexity": ProviderInfo(
        name="perplexity",
        label="Perplexity",
        needs_api_key=True,
        default_base_url="https://api.perplexity.ai",
        known_models=["sonar-pro", "sonar", "sonar-reasoning"],
        provider_type="api",
    ),
    "cohere": ProviderInfo(
        name="cohere",
        label="Cohere",
        needs_api_key=True,
        default_base_url="https://api.cohere.com/compatibility/v1",
        known_models=["command-r-plus-08-2024", "command-r-08-2024"],
        provider_type="api",
    ),
    "together": ProviderInfo(
        name="together",
        label="Together AI",
        needs_api_key=True,
        default_base_url="https://api.together.xyz/v1",
        known_models=[],  # too many to curate; user enters model slug
        provider_type="api",
    ),
    "bailian": ProviderInfo(
        name="bailian",
        label="Alibaba Bailian (China)",
        needs_api_key=True,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        known_models=[
            "qwen3-max", "qwen3-235b-a22b", "qwen3-32b", "qwen3-14b", "qwen3-8b",
            "qwen3.5-plus", "qwen3.5-flash",
            "qwq-plus",
        ],
        provider_type="api",
    ),
    "alibaba_cloud": ProviderInfo(
        name="alibaba_cloud",
        label="Alibaba Cloud (international)",
        needs_api_key=True,
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        known_models=[
            "qwen3-max", "qwen3-235b-a22b", "qwen3-32b", "qwen3-14b", "qwen3-8b",
            "qwen3.5-plus", "qwen3.5-flash",
            "qwq-plus",
        ],
        provider_type="api",
    ),
    "minimax": ProviderInfo(
        name="minimax",
        label="MiniMax",
        needs_api_key=True,
        default_base_url="https://api.minimax.io/v1",
        known_models=[
            "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
        ],
        provider_type="api",
    ),
    "kimi": ProviderInfo(
        name="kimi",
        label="Kimi (Moonshot, China)",
        needs_api_key=True,
        default_base_url="https://api.moonshot.cn/v1",
        known_models=[
            "kimi-k2.5", "kimi-k2-thinking", "kimi-k2-thinking-turbo",
            "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k",
        ],
        provider_type="api",
    ),
    "openrouter": ProviderInfo(
        name="openrouter",
        label="OpenRouter (gateway)",
        needs_api_key=True,
        default_base_url="https://openrouter.ai/api/v1",
        known_models=[],  # too many to curate; user types model slug
        provider_type="api",
    ),
    # ── OAuth ──────────────────────────────────────────────────────────────
    "github_copilot": ProviderInfo(
        name="github_copilot",
        label="GitHub Copilot",
        needs_api_key=True,  # GitHub PAT or device-flow OAuth token
        default_base_url="https://api.githubcopilot.com",
        known_models=[
            "gpt-5.5",
            "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
            "gpt-5.3-codex", "gpt-5.2-codex", "gpt-5.2",
            "gpt-5-mini", "gpt-4.1",
            "claude-sonnet-4.6", "claude-sonnet-4.5",
            "claude-opus-4.7", "claude-opus-4.6", "claude-opus-4.6-fast",
            "claude-opus-4.5", "claude-haiku-4.5",
            "gemini-3.1-pro", "gemini-3-flash", "gemini-2.5-pro",
            "grok-code-fast-1",
            "raptor-mini", "goldeneye",
        ],
        provider_type="oauth",
    ),
    "chatgpt": ProviderInfo(
        name="chatgpt",
        label="ChatGPT (OpenAI OAuth)",
        needs_api_key=True,  # OpenAI OAuth app token or API key from platform.openai.com
        default_base_url="https://api.openai.com/v1",
        known_models=[
            "gpt-5.5",
            "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
            "gpt-5.2", "gpt-5-mini", "gpt-5-nano",
            "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "gpt-4o", "gpt-4o-mini",
        ],
        provider_type="oauth",
    ),
    # ── Custom ─────────────────────────────────────────────────────────────
    "custom": ProviderInfo(
        name="custom",
        label="Custom OpenAI-compatible endpoint",
        needs_api_key=True,
        default_base_url="",
        known_models=[],  # user-defined
        provider_type="api",
    ),
}


def list_providers() -> list[ProviderInfo]:
    return list(REGISTRY.values())


def get_provider(name: str) -> ProviderInfo | None:
    return REGISTRY.get(name)
