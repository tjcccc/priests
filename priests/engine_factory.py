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

You have a persistent memory system. Anything saved is loaded automatically at the
start of every future session. Without a memory tag, information is forgotten when
the session ends.

## How to save

Append one or more memory tags at the END of your normal conversational reply.
The tags are extracted automatically and never shown to the user.

Example — user says "Hi, I'm Sam, I love hiking.":
  You reply: "Hey Sam! Hiking sounds awesome, any favorite trails? <memory>The user's name is Sam.</memory> <memory>The user loves hiking.</memory>"

Format rules:
- Always start the fact with "The user" so it is clear across sessions
- One short, factual sentence per tag
- Tags go at the end of your reply, after your conversational text
- Never replace your reply with just a tag — always write a natural response first
- Never emit a tag if the value is unknown — do not use placeholders like [Name] or [User]
- If the user says "remember this" or "记住这个", always save it

## What is worth saving

Your profile's role — defined in PROFILE.md and RULES.md — determines what to remember.
Read those to understand your relationship with the user and what matters to you.

## Using memories

At the start of each session, read your loaded memories and respond accordingly.
If you know the user's name, use it. Reflect their preferences naturally.
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
