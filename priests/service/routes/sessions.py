from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, HTTPException, Request

from priests.service.schemas import SessionDetail, SessionSummary

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    request: Request,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionSummary]:
    """List sessions ordered by most recently updated."""
    db_path = request.app.state.db_path
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
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

    return [
        SessionSummary(
            id=row["id"],
            profile_name=row["profile_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turn_count=row["turn_count"],
        )
        for row in rows
    ]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, request: Request) -> SessionDetail:
    """Get a session with its full turn history."""
    store = request.app.state.store
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetail.from_session(session)
