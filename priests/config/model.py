from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


def _default_profiles_dir() -> Path:
    return Path.home() / ".priests" / "profiles"


def _default_sessions_db() -> Path:
    return Path.home() / ".priests" / "sessions.db"


def _default_uploads_dir() -> Path:
    return Path.home() / ".priests" / "uploads"


class DefaultsConfig(BaseModel):
    provider: str | None = None   # set by `priests init`
    model: str | None = None      # set by `priests init`
    profile: str = "default"
    timeout_seconds: float = 120.0
    max_output_tokens: int | None = None
    # When False, injects {"think": False} into provider_options (Qwen3 / Ollama)
    think: bool = False


class PathsConfig(BaseModel):
    profiles_dir: Path = Field(default_factory=_default_profiles_dir)
    sessions_db: Path = Field(default_factory=_default_sessions_db)
    uploads_dir: Path = Field(default_factory=_default_uploads_dir)
    log_file: Path | None = None


class ServiceConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8777


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"


class OpenAICompatConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    use_proxy: bool = False


class AnthropicConfig(BaseModel):
    api_key: str = ""
    use_proxy: bool = False


class ProxyConfig(BaseModel):
    url: str = ""


class ProvidersConfig(BaseModel):
    # Ollama is always present (local, no key needed).
    # All API providers default to None and are only written to priests.toml
    # once the user has configured them.
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    llamacpp: OllamaConfig = Field(default_factory=lambda: OllamaConfig(base_url="http://localhost:8080"))
    lmstudio: OllamaConfig = Field(default_factory=lambda: OllamaConfig(base_url="http://localhost:1234/v1"))
    openai: OpenAICompatConfig | None = None
    anthropic: AnthropicConfig | None = None
    gemini: OpenAICompatConfig | None = None
    bailian: OpenAICompatConfig | None = None
    alibaba_cloud: OpenAICompatConfig | None = None
    minimax: OpenAICompatConfig | None = None
    deepseek: OpenAICompatConfig | None = None
    kimi: OpenAICompatConfig | None = None
    groq: OpenAICompatConfig | None = None
    openrouter: OpenAICompatConfig | None = None
    mistral: OpenAICompatConfig | None = None
    together: OpenAICompatConfig | None = None
    perplexity: OpenAICompatConfig | None = None
    cohere: OpenAICompatConfig | None = None
    github_copilot: OpenAICompatConfig | None = None
    chatgpt: OpenAICompatConfig | None = None
    custom: OpenAICompatConfig | None = None


class ModelsConfig(BaseModel):
    options: list[str] = Field(default_factory=list)  # stored as "provider/model"


class WebSearchConfig(BaseModel):
    enabled: bool = True
    max_results: int = 5


class MemoryConfig(BaseModel):
    size_limit: int = 50000   # max characters in auto_short.md on disk; 0 = unlimited
    context_limit: int = 0    # max combined characters of all three memory files
                              # injected into the system prompt per turn; 0 = unlimited.
                              # When exceeded, auto_short sections are dropped
                              # oldest-first until the block fits. user.md and notes.md
                              # are never truncated at injection time.
    flat_line_cap: int = 0    # soft line cap for user.md and notes.md, enforced during
                              # consolidation via a prompt hint. 0 = no hint.


class AppConfig(BaseModel):
    default: DefaultsConfig = Field(default_factory=DefaultsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    proxy: ProxyConfig | None = None
