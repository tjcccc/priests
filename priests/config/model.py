from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


def _default_profiles_dir() -> Path:
    return Path.home() / ".priests" / "profiles"


def _default_sessions_db() -> Path:
    return Path.home() / ".priests" / "sessions.db"


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
    log_file: Path | None = None


class ServiceConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8777


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"


class ProvidersConfig(BaseModel):
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


class MemoryConfig(BaseModel):
    limit: int = 50  # max auto_*.md files to keep per profile; 0 = unlimited


class AppConfig(BaseModel):
    default: DefaultsConfig = Field(default_factory=DefaultsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
