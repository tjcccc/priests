from __future__ import annotations

from pathlib import Path

from priest import PriestEngine
from priest.profile.default_profile import IDENTITY, RULES
from priest.profile.loader import FilesystemProfileLoader
from priest.providers.ollama_provider import OllamaProvider
from priest.session.sqlite_store import SqliteSessionStore

from priests.config.model import AppConfig


_PRIESTS_MD = "PRIESTS.md"

_PRIESTS_MD_DEFAULT = """\
# Memory System

You have a persistent memory system. Anything you save will be loaded automatically
at the start of every future session with this profile. Without a memory tag,
information is forgotten when the session ends.

## How to save

Include a memory tag anywhere in your response:

<memory>The user's name is Jack.</memory>

Format rules:
- Always start with "The user" so the memory is unambiguous across sessions
- One short, factual sentence per tag
- Multiple tags in one response are fine — one per distinct fact
- Do not tag your own statements or conversational filler
- If the user explicitly says "remember this" or "记住这个", always save it

## What is worth saving

Your profile's role and character — defined in PROFILE.md and RULES.md — determine
what is meaningful to remember. Read those to understand what you should care about.
A close friend remembers personal details; a focused assistant may only note work context.

## Using memories

At the start of each session, check your loaded memories and respond accordingly.
If you know the user's name, use it. Reflect their known preferences naturally.
"""


def _bootstrap_profiles(profiles_root: Path) -> None:
    """Scaffold profiles_root, default profile, and PRIESTS.md on first run."""
    default_dir = profiles_root / "default"
    if not default_dir.exists():
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / "PROFILE.md").write_text(IDENTITY)
        (default_dir / "RULES.md").write_text(RULES)
        (default_dir / "CUSTOM.md").write_text("")
        (default_dir / "memories").mkdir()

    # Bootstrap PRIESTS.md unconditionally — handles v0.1 → v0.2 upgrade.
    guide_path = profiles_root.parent / _PRIESTS_MD
    if not guide_path.exists():
        guide_path.write_text(_PRIESTS_MD_DEFAULT)


def load_global_guide(config: AppConfig) -> str | None:
    """Return ~/.priests/PRIESTS.md contents, or None if missing or empty."""
    path = config.paths.profiles_dir.expanduser().parent / _PRIESTS_MD
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        return text if text else None
    return None


class NotInitializedError(RuntimeError):
    pass


async def build_engine(config: AppConfig) -> tuple[PriestEngine, SqliteSessionStore]:
    """Construct a PriestEngine and SqliteSessionStore from AppConfig.

    Returns the store UN-initialized — the caller is responsible for its lifecycle:
      CLI:     async with store: response = await engine.run(...)
      FastAPI: lifespan calls await store.init() / store.close()
    """
    if not config.default.provider or not config.default.model:
        raise NotInitializedError(
            "priests is not initialized. Run 'priests init' to set up."
        )

    profiles_root = config.paths.profiles_dir.expanduser()
    sessions_db = config.paths.sessions_db.expanduser()

    # Ensure the sessions DB parent directory exists
    sessions_db.parent.mkdir(parents=True, exist_ok=True)

    # Bootstrap profiles_root and default profile on first run
    _bootstrap_profiles(profiles_root)

    profile_loader = FilesystemProfileLoader(profiles_root=profiles_root)

    store = SqliteSessionStore(db_path=sessions_db)

    adapters: dict = {
        "ollama": OllamaProvider(base_url=config.providers.ollama.base_url),
    }

    engine = PriestEngine(
        profile_loader=profile_loader,
        session_store=store,
        adapters=adapters,
    )

    return engine, store
