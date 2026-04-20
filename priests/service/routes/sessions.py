from __future__ import annotations

import shutil
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, HTTPException, Request

from priests.service.schemas import SessionDetail, SessionSummary, TurnOut

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    request: Request,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionSummary]:
    """List sessions ordered by pinned first, then most recently updated."""
    db_path = request.app.state.db_path
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                "SELECT key FROM ui_meta WHERE key LIKE 'session_pinned:%'"
            )
            pinned_ids = {row["key"][15:] for row in await cursor.fetchall()}
        except Exception:
            pinned_ids = set()

        cursor = await db.execute(
            """
            SELECT s.id, s.profile_name, s.created_at, s.updated_at,
                   COUNT(t.rowid) AS turn_count
            FROM sessions s
            LEFT JOIN turns t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()

    results = [
        SessionSummary(
            id=row["id"],
            profile_name=row["profile_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turn_count=row["turn_count"],
            pinned=row["id"] in pinned_ids,
        )
        for row in rows
    ]
    # SQL already orders by updated_at DESC; stable sort puts pinned first
    results.sort(key=lambda s: not s.pinned)
    return results


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, request: Request) -> SessionDetail:
    """Get a session with its full turn history including model/timing metadata."""
    store = request.app.state.store
    db_path = str(request.app.state.db_path)
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Load turn_meta keyed by raw timestamp string
    meta_map: dict[str, dict] = {}
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT turn_timestamp, model, elapsed_ms FROM turn_meta WHERE session_id = ?",
                (session_id,),
            )
            for row in await cursor.fetchall():
                meta_map[row["turn_timestamp"]] = {
                    "model": row["model"],
                    "elapsed_ms": row["elapsed_ms"],
                }
    except Exception:
        pass

    turns = []
    for t in session.turns:
        ts_str = t.timestamp.isoformat()
        meta = meta_map.get(ts_str)
        turns.append(TurnOut(
            role=t.role,
            content=t.content,
            timestamp=t.timestamp,
            model=meta["model"] if meta else None,
            elapsed_ms=meta["elapsed_ms"] if meta else None,
        ))

    return SessionDetail(
        id=session.id,
        profile_name=session.profile_name,
        created_at=session.created_at,
        updated_at=session.updated_at,
        turns=turns,
    )


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict:
    """Delete a session and all its associated uploads from DB and disk."""
    db_path = str(request.app.state.db_path)
    config = request.app.state.config

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Collect upload file paths before deleting
        cursor = await db.execute(
            "SELECT file_path FROM uploads WHERE session_id = ?", (session_id,)
        )
        file_paths = [row["file_path"] for row in await cursor.fetchall()]

        await db.execute("DELETE FROM uploads WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM turn_meta WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.execute("DELETE FROM ui_meta WHERE key = ?", (f"session_title:{session_id}",))
        await db.execute("DELETE FROM ui_meta WHERE key = ?", (f"session_pinned:{session_id}",))
        await db.commit()

    # Delete upload files from disk
    for fp in file_paths:
        try:
            Path(fp).unlink(missing_ok=True)
        except Exception:
            pass
    # Delete the session upload directory if empty
    session_upload_dir = config.paths.uploads_dir.expanduser() / session_id
    if session_upload_dir.exists():
        try:
            shutil.rmtree(str(session_upload_dir))
        except Exception:
            pass

    return {"ok": True}
