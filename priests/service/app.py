from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from priests import __version__
from priests.config.model import AppConfig
from priests.engine_factory import build_engine
from priests.service.routes.health import router as health_router
from priests.service.routes.run import router as run_router
from priests.service.routes.sessions import router as session_router
from priests.service.routes.ui import router as ui_router
from priests.service.routes.ui import _ensure_table

_UI_DIST = Path(__file__).parent.parent / "ui" / "dist"


def create_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, store = await build_engine(config)
        await store.init()
        app.state.engine = engine
        app.state.store = store
        app.state.config = config
        # Expose raw db_path for the sessions list query
        app.state.db_path = config.paths.sessions_db.expanduser()
        await _ensure_table(str(app.state.db_path))
        yield
        await store.close()

    app = FastAPI(
        title="priests",
        version=__version__,
        description="AI dispatch service.",
        lifespan=lifespan,
    )

    app.include_router(health_router, tags=["health"])
    app.include_router(run_router, prefix="/v1", tags=["run"])
    app.include_router(session_router, prefix="/v1", tags=["sessions"])
    app.include_router(ui_router, prefix="/v1", tags=["ui"])

    if _UI_DIST.exists():
        app.mount("/", StaticFiles(directory=str(_UI_DIST), html=True), name="ui")

    return app
