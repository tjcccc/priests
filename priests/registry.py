from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderInfo:
    name: str             # key used in config and adapters
    label: str            # human-readable display name
    needs_api_key: bool
    default_base_url: str
    # None  → fetch dynamically at runtime (Ollama)
    # []    → no curated list; user enters model name as free text
    # [..] → curated list shown in arrow selector
    known_models: list[str] | None = field(default_factory=list)


REGISTRY: dict[str, ProviderInfo] = {
    "ollama": ProviderInfo(
        name="ollama",
        label="Ollama  (local models)",
        needs_api_key=False,
        default_base_url="http://localhost:11434",
        known_models=None,  # fetched dynamically
    ),
    "openai": ProviderInfo(
        name="openai",
        label="OpenAI",
        needs_api_key=True,
        default_base_url="https://api.openai.com/v1",
        known_models=[
            "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "gpt-4o", "gpt-4o-mini",
            "o4-mini", "o3", "o3-mini",
        ],
    ),
    "anthropic": ProviderInfo(
        name="anthropic",
        label="Anthropic Claude",
        needs_api_key=True,
        default_base_url="https://api.anthropic.com",
        known_models=[
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ],
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
    ),
    "bailian": ProviderInfo(
        name="bailian",
        label="Alibaba Bailian  (China mainland)",
        needs_api_key=True,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        known_models=[
            "qwen3-max", "qwen3.5-plus", "qwen3.5-flash",
            "qwen-max", "qwen-plus", "qwen-turbo", "qwen-long",
            "qwq-plus",
            "qwen3-235b-a22b", "qwen3-32b", "qwen3-14b", "qwen3-8b",
        ],
    ),
    "alibaba_cloud": ProviderInfo(
        name="alibaba_cloud",
        label="Alibaba Cloud  (international)",
        needs_api_key=True,
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        known_models=[
            "qwen3-max", "qwen3.5-plus", "qwen3.5-flash",
            "qwen-max", "qwen-plus", "qwen-turbo", "qwen-long",
            "qwq-plus",
            "qwen3-235b-a22b", "qwen3-32b", "qwen3-14b", "qwen3-8b",
        ],
    ),
    "minimax": ProviderInfo(
        name="minimax",
        label="MiniMax",
        needs_api_key=True,
        default_base_url="https://api.minimax.io/v1",
        known_models=[
            "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
            "MiniMax-M2.1", "MiniMax-M2.1-highspeed",
            "MiniMax-M2",
        ],
    ),
    "deepseek": ProviderInfo(
        name="deepseek",
        label="DeepSeek  (China mainland)",
        needs_api_key=True,
        default_base_url="https://api.deepseek.com/v1",
        known_models=[
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    ),
    "kimi": ProviderInfo(
        name="kimi",
        label="Kimi  (Moonshot, China mainland)",
        needs_api_key=True,
        default_base_url="https://api.moonshot.cn/v1",
        known_models=[
            "kimi-k2.5",
            "kimi-k2-thinking",
            "kimi-k2-thinking-turbo",
            "moonshot-v1-128k",
            "moonshot-v1-32k",
            "moonshot-v1-8k",
        ],
    ),
    "groq": ProviderInfo(
        name="groq",
        label="Groq  (fast open-model inference)",
        needs_api_key=True,
        default_base_url="https://api.groq.com/openai/v1",
        known_models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "qwen/qwen3-32b",
        ],
    ),
    "openrouter": ProviderInfo(
        name="openrouter",
        label="OpenRouter  (multi-provider gateway)",
        needs_api_key=True,
        default_base_url="https://openrouter.ai/api/v1",
        known_models=[],  # too many to curate; user types the model slug
    ),
    "custom": ProviderInfo(
        name="custom",
        label="Custom OpenAI-compatible endpoint",
        needs_api_key=True,
        default_base_url="",
        known_models=[],  # user-defined
    ),
}


def list_providers() -> list[ProviderInfo]:
    return list(REGISTRY.values())


def get_provider(name: str) -> ProviderInfo | None:
    return REGISTRY.get(name)
