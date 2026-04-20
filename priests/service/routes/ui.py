from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Request
from pydantic import BaseModel

from priests.registry import REGISTRY

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _ensure_table(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS ui_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        await db.commit()


async def _upsert(db_path: str, key: str, value: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO ui_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# /v1/ui/meta  — all custom titles + emojis in one shot
# ---------------------------------------------------------------------------

@router.get("/ui/meta")
async def get_ui_meta(request: Request) -> dict:
    """Return all custom session titles, profile emojis, and pinned session IDs."""
    db_path = str(request.app.state.db_path)
    await _ensure_table(db_path)
    result: dict = {"session_titles": {}, "profile_emojis": {}, "pinned_sessions": []}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT key, value FROM ui_meta")
        for row in await cursor.fetchall():
            key, value = row["key"], row["value"]
            if key.startswith("session_title:"):
                result["session_titles"][key[14:]] = value
            elif key.startswith("profile_emoji:"):
                result["profile_emojis"][key[14:]] = value
            elif key.startswith("session_pinned:"):
                result["pinned_sessions"].append(key[15:])
    return result


class TitleIn(BaseModel):
    title: str


@router.put("/ui/sessions/{session_id}/title")
async def set_session_title(session_id: str, body: TitleIn, request: Request) -> dict:
    """Persist a custom session title."""
    db_path = str(request.app.state.db_path)
    await _ensure_table(db_path)
    await _upsert(db_path, f"session_title:{session_id}", body.title)
    return {"ok": True}


class EmojiIn(BaseModel):
    emoji: str


@router.put("/ui/sessions/{session_id}/pin")
async def toggle_session_pin(session_id: str, request: Request) -> dict:
    """Toggle pin for a session. Returns {pinned: bool}."""
    db_path = str(request.app.state.db_path)
    await _ensure_table(db_path)
    key = f"session_pinned:{session_id}"
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT value FROM ui_meta WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row:
            await db.execute("DELETE FROM ui_meta WHERE key = ?", (key,))
            await db.commit()
            return {"pinned": False}
        else:
            await db.execute(
                "INSERT INTO ui_meta (key, value) VALUES (?, ?)", (key, "true")
            )
            await db.commit()
            return {"pinned": True}


@router.put("/ui/profiles/{profile_name}/emoji")
async def set_profile_emoji(profile_name: str, body: EmojiIn, request: Request) -> dict:
    """Persist a custom profile emoji."""
    db_path = str(request.app.state.db_path)
    await _ensure_table(db_path)
    await _upsert(db_path, f"profile_emoji:{profile_name}", body.emoji)
    return {"ok": True}


# ---------------------------------------------------------------------------
# /v1/ui/models  — available providers and configured model options
# ---------------------------------------------------------------------------

@router.get("/ui/models")
async def get_models(request: Request) -> dict:
    """Return configured model options and the full provider registry."""
    config = request.app.state.config
    options = []
    for opt in config.models.options:
        parts = opt.split("/", 1)
        if len(parts) == 2:
            options.append({"provider": parts[0], "model": parts[1]})
    providers = [
        {
            "name": name,
            "label": info.label,
            "known_models": info.known_models,  # None = dynamic, [] = free-text
        }
        for name, info in REGISTRY.items()
    ]
    return {
        "default_provider": config.default.provider,
        "default_model": config.default.model,
        "configured_options": options,
        "providers": providers,
    }
