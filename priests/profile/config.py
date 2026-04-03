from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from pydantic import BaseModel


class ProfileConfig(BaseModel):
    memories: bool = True
    memories_limit: int | None = None  # overrides global [memory].limit when set


def load_profile_config(profiles_dir: Path, profile: str) -> ProfileConfig:
    """Load profile.toml for a profile, returning defaults if absent."""
    path = profiles_dir.expanduser() / profile / "profile.toml"
    if not path.exists():
        return ProfileConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ProfileConfig.model_validate(data)
