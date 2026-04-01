from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w

from priests.config.model import AppConfig

# Config file search order (first found wins)
_CONFIG_SEARCH_PATHS = [
    Path.home() / ".priests" / "priests.toml",
    Path.home() / ".config" / "priests" / "priests.toml",   # XDG fallback
]


def _find_config_file() -> Path | None:
    for p in _CONFIG_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def _apply_env_overrides(raw: dict) -> dict:
    """Overlay PRIESTS_* env vars onto the raw config dict.

    Double-underscore separates nesting levels:
        PRIESTS_DEFAULT__MODEL      → raw["default"]["model"]
        PRIESTS_SERVICE__PORT       → raw["service"]["port"]
        PRIESTS_PROVIDERS__OLLAMA__BASE_URL → raw["providers"]["ollama"]["base_url"]
    """
    prefix = "PRIESTS_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix) :].lower().split("__")
        target = raw
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        leaf = parts[-1]
        # Attempt numeric coercion so port/timeout stay typed correctly
        if value.isdigit():
            target[leaf] = int(value)
        else:
            try:
                target[leaf] = float(value)
            except ValueError:
                target[leaf] = value
    return raw


def is_initialized(config_path: Path | None = None) -> bool:
    """Return True if a config file exists and provider + model are set."""
    path = config_path or _find_config_file()
    if not path or not path.exists():
        return False
    config = load_config(config_path)
    return bool(config.default.provider and config.default.model)


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration with priority: env vars > config file > defaults."""
    raw: dict = {}

    path = config_path or _find_config_file()
    if path and path.exists():
        raw = tomllib.loads(path.read_text())

    raw = _apply_env_overrides(raw)
    return AppConfig.model_validate(raw)


def _strip_none(obj):
    """Recursively remove None values — TOML has no null type."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    return obj


def save_config(config: AppConfig, config_path: Path | None = None) -> Path:
    """Serialize AppConfig back to a TOML file and return the path written."""
    path = config_path or _CONFIG_SEARCH_PATHS[0]
    path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to a plain dict suitable for tomli_w (no Path objects, no None)
    raw = _strip_none(config.model_dump(mode="json"))
    path.write_bytes(tomli_w.dumps(raw).encode())
    return path


def set_config_value(key: str, value: str, config_path: Path | None = None) -> Path:
    """Set a dotted key (e.g. 'default.model') in the config file and save it."""
    config = load_config(config_path)
    raw = config.model_dump(mode="json")

    parts = key.split(".")
    target = raw
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            raise KeyError(f"Unknown config key: {key!r}")
        target = target[part]

    leaf = parts[-1]
    if leaf not in target:
        raise KeyError(f"Unknown config key: {key!r}")

    current = target[leaf]
    if isinstance(current, bool):
        target[leaf] = value.lower() in ("true", "1", "yes")
    elif isinstance(current, int):
        target[leaf] = int(value)
    elif isinstance(current, float):
        target[leaf] = float(value)
    else:
        target[leaf] = value

    updated = AppConfig.model_validate(raw)
    return save_config(updated, config_path)
