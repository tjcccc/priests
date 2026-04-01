from __future__ import annotations

from pathlib import Path

from priest import PriestEngine
from priest.profile.default_profile import IDENTITY, RULES
from priest.profile.loader import FilesystemProfileLoader
from priest.providers.ollama_provider import OllamaProvider
from priest.session.sqlite_store import SqliteSessionStore

from priests.config.model import AppConfig


def _bootstrap_profiles(profiles_root: Path) -> None:
    """Scaffold profiles_root and the default profile on first run."""
    default_dir = profiles_root / "default"
    if default_dir.exists():
        return

    default_dir.mkdir(parents=True, exist_ok=True)
    (default_dir / "PROFILE.md").write_text(IDENTITY)
    (default_dir / "RULES.md").write_text(RULES)
    (default_dir / "CUSTOM.md").write_text("")
    (default_dir / "memories").mkdir()


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
