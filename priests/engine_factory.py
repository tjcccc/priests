from __future__ import annotations

from pathlib import Path

from priest import PriestEngine
from priest.profile.default_profile import IDENTITY, RULES
from priest.profile.loader import FilesystemProfileLoader
from priest.providers.anthropic_provider import AnthropicProvider
from priest.providers.ollama_provider import OllamaProvider
from priest.providers.openai_compat_provider import OpenAICompatProvider
from priest.session.sqlite_store import SqliteSessionStore

from priests.config.model import AppConfig


_PRIESTS_MD = "PRIESTS.md"

_PRIESTS_MD_DEFAULT = """\
# Memory System

You have a persistent memory system with three categories. Saved facts are loaded
automatically at the start of every future session. Without a tag, information is
forgotten when the session ends.

## Memory categories

| Type | Tag | File | Use for |
|------|-----|------|---------|
| auto (default) | `<memory>` | daily log | casual observations, conversation context |
| user | `<memory type="user">` | user profile | stable facts about the user: name, preferences, background |
| note | `<memory type="note">` | notes | role-defined important things: birthdays, key constraints, etc. |

## How to save

Append memory tags at the END of your normal conversational reply. The tags are
extracted automatically and never shown to the user.

Example — user says "Hi, I'm Sam, I love hiking.":
  Your reply: "Hey Sam! Hiking sounds fun. <memory type="user">The user's name is Sam.</memory> <memory type="user">The user loves hiking.</memory>"

Example — casual observation:
  Your reply: "Got it! <memory>The user seems to be in a hurry today.</memory>"

Format rules:
- Always start the fact with "The user" so it is unambiguous across sessions
- One short factual sentence per tag; multiple tags per reply are fine
- Tags go at the end of your reply, after your conversational text
- Never replace your reply with a tag alone — always write a natural response first
- Never emit a tag when the value is unknown — no placeholders like [Name] or [Unknown]
- Only save facts about the human user — never tag your own name, traits, or statements
- If the user says "remember this" or "记住这个", always save it

## What is worth saving

Your profile's role and character — defined in PROFILE.md and RULES.md — determine
what categories to use and what is meaningful to remember.

## Using memories

At the start of each session, read your loaded memories and respond accordingly.
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

    # Bootstrap profile.toml — handles v0.2 upgrade for existing installs.
    profile_toml = default_dir / "profile.toml"
    if not profile_toml.exists():
        profile_toml.write_text("memories = true\n")

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

    p = config.providers
    proxy_url = config.proxy.url if (config.proxy and config.proxy.url) else None

    adapters: dict = {
        "ollama": OllamaProvider(base_url=p.ollama.base_url),
    }

    _compat = [
        ("openai", p.openai),
        ("gemini", p.gemini),
        ("bailian", p.bailian),
        ("alibaba_cloud", p.alibaba_cloud),
        ("minimax", p.minimax),
        ("deepseek", p.deepseek),
        ("kimi", p.kimi),
        ("groq", p.groq),
        ("openrouter", p.openrouter),
        ("custom", p.custom),
    ]
    for name, cfg in _compat:
        if cfg and cfg.base_url:
            proxy = proxy_url if (cfg.use_proxy and proxy_url) else None
            adapters[name] = OpenAICompatProvider(name, cfg.base_url, cfg.api_key, proxy=proxy)

    if p.anthropic and p.anthropic.api_key:
        proxy = proxy_url if (p.anthropic.use_proxy and proxy_url) else None
        adapters["anthropic"] = AnthropicProvider(p.anthropic.api_key, proxy=proxy)

    engine = PriestEngine(
        profile_loader=profile_loader,
        session_store=store,
        adapters=adapters,
    )

    return engine, store
