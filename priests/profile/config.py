from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from pydantic import BaseModel

from priests.config.model import AppConfig


class ProfileConfig(BaseModel):
    memories: bool = True
    memories_limit: int | None = None  # overrides global [memory].size_limit when set
    provider: str | None = None
    model: str | None = None


def load_profile_config(profiles_dir: Path, profile: str) -> ProfileConfig:
    """Load profile.toml for a profile, returning defaults if absent."""
    path = profiles_dir.expanduser() / profile / "profile.toml"
    if not path.exists():
        return ProfileConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ProfileConfig.model_validate(data)


def resolve_provider_model(
    config: AppConfig,
    profile: str,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve provider/model precedence: explicit request, profile pair, global default."""
    profile_cfg = load_profile_config(config.paths.profiles_dir, profile)
    has_profile_model = bool(profile_cfg.provider and profile_cfg.model)
    base_provider = profile_cfg.provider if has_profile_model else config.default.provider
    base_model = profile_cfg.model if has_profile_model else config.default.model
    return provider or base_provider, model or base_model
