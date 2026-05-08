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
# priests

You are running inside the priests AI dispatch system.
Your profile (PROFILE.md, RULES.md, CUSTOM.md) defines your identity and role.
Your memory files (user.md, preferences.md, auto_short.md) carry approved facts across sessions.

Memory instructions for each session are provided dynamically in the system context.
"""


def _bootstrap_profiles(profiles_root: Path) -> None:
    """Scaffold profiles_root, default profile, and PRIESTS.md on first run."""
    default_dir = profiles_root / "default"
    if not default_dir.exists():
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / "PROFILE.md").write_text(IDENTITY)
        (default_dir / "RULES.md").write_text(RULES)
        (default_dir / "CUSTOM.md").write_text("")
        _scaffold_memories(default_dir / "memories")

    # Bootstrap profile.toml — handles upgrade for existing installs.
    profile_toml = default_dir / "profile.toml"
    if not profile_toml.exists():
        profile_toml.write_text("memories = true\n")

    # Ensure new memory files exist for upgraded installs without overwriting
    # legacy notes.md or user-edited memory files.
    _scaffold_memories(default_dir / "memories")

    # Always overwrite PRIESTS.md to keep it current across upgrades.
    guide_path = profiles_root.parent / _PRIESTS_MD
    guide_path.write_text(_PRIESTS_MD_DEFAULT)


def _scaffold_memories(memories_dir: Path) -> None:
    """Create the standard memory file stubs in a profile's memories directory."""
    memories_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in (
        ("user.md", "# User\n\n"),
        ("preferences.md", "# Preferences\n\n"),
        ("auto_short.md", "# Short Memories\n\n"),
    ):
        path = memories_dir / fname
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    (memories_dir / "pending").mkdir(exist_ok=True)


def load_global_guide(config: AppConfig) -> str | None:
    """Return ~/.priests/PRIESTS.md contents, or None if missing or empty."""
    path = config.paths.profiles_dir.expanduser().parent / _PRIESTS_MD
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        return text if text else None
    return None


class NotInitializedError(RuntimeError):
    pass


def build_adapters(config: AppConfig) -> dict:
    """Build the provider adapters dict from AppConfig.

    Separated from build_engine() so the config PATCH route can hot-reload
    adapters without touching the session store.
    """
    p = config.providers
    proxy_url = config.proxy.url if (config.proxy and config.proxy.url) else None

    adapters: dict = {
        "ollama": OllamaProvider(base_url=p.ollama.base_url),
        "llamacpp": OpenAICompatProvider("llamacpp", p.llamacpp.base_url, "", proxy=None),
        "lmstudio": OpenAICompatProvider("lmstudio", p.lmstudio.base_url, "", proxy=None),
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
        ("mistral", p.mistral),
        ("together", p.together),
        ("perplexity", p.perplexity),
        ("cohere", p.cohere),
        ("github_copilot", p.github_copilot),
        ("chatgpt", p.chatgpt),
        ("custom", p.custom),
    ]
    for name, cfg in _compat:
        if cfg and cfg.base_url:
            proxy = proxy_url if (cfg.use_proxy and proxy_url) else None
            adapters[name] = OpenAICompatProvider(name, cfg.base_url, cfg.api_key, proxy=proxy)

    if p.anthropic and p.anthropic.api_key:
        proxy = proxy_url if (p.anthropic.use_proxy and proxy_url) else None
        adapters["anthropic"] = AnthropicProvider(p.anthropic.api_key, proxy=proxy)

    return adapters


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

    profile_loader = FilesystemProfileLoader(profiles_root=profiles_root, include_memories=False)
    store = SqliteSessionStore(db_path=sessions_db)

    engine = PriestEngine(
        profile_loader=profile_loader,
        session_store=store,
        adapters=build_adapters(config),
    )

    return engine, store
