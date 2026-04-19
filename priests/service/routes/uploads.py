from __future__ import annotations

import base64
import io
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter()

_MEDIA_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

_CREATE_UPLOADS = """
CREATE TABLE IF NOT EXISTS uploads (
    uuid        TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    turn_timestamp TEXT,
    media_type  TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""


def _compress(data: bytes, media_type: str) -> bytes:
    try:
        from PIL import Image  # type: ignore
        img = Image.open(io.BytesIO(data))
        if media_type == "image/jpeg" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        fmt = "JPEG" if media_type == "image/jpeg" else (img.format or "PNG")
        img.save(out, format=fmt, quality=80, optimize=True)
        return out.getvalue()
    except Exception:
        return data


async def ensure_uploads_table(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_CREATE_UPLOADS)
        await db.commit()


async def update_turn_timestamps(db_path: str, session_id: str, upload_uuids: list[str]) -> None:
    """Set turn_timestamp on uploads after a turn is stored in the session."""
    if not upload_uuids or not session_id:
        return
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT timestamp FROM turns WHERE session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row:
            ts = row["timestamp"]
            placeholders = ",".join("?" * len(upload_uuids))
            await db.execute(
                f"UPDATE uploads SET turn_timestamp = ? WHERE uuid IN ({placeholders})",
                [ts, *upload_uuids],
            )
            await db.commit()


async def load_upload_images(db_path: str, upload_uuids: list[str]) -> list[tuple[bytes, str]]:
    """Return (file_bytes, media_type) for each UUID, skipping missing files."""
    if not upload_uuids:
        return []
    result = []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" * len(upload_uuids))
        cursor = await db.execute(
            f"SELECT uuid, file_path, media_type FROM uploads WHERE uuid IN ({placeholders})",
            upload_uuids,
        )
        rows = {row["uuid"]: row for row in await cursor.fetchall()}
    for uid in upload_uuids:
        row = rows.get(uid)
        if not row:
            continue
        path = Path(row["file_path"])
        if path.exists():
            result.append((path.read_bytes(), row["media_type"]))
    return result


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class UploadIn(BaseModel):
    data: str           # base64-encoded image bytes
    media_type: str = "image/jpeg"
    session_id: str
    batch_id: str       # same for all images attached in one action


@router.post("/uploads")
async def create_upload(body: UploadIn, request: Request) -> dict:
    config = request.app.state.config
    db_path = str(request.app.state.db_path)
    await ensure_uploads_table(db_path)

    raw = base64.b64decode(body.data)
    compressed = _compress(raw, body.media_type)

    uid = str(_uuid.uuid4())
    ext = _MEDIA_TO_EXT.get(body.media_type, "bin")
    uploads_dir: Path = config.paths.uploads_dir.expanduser() / body.session_id
    uploads_dir.mkdir(parents=True, exist_ok=True)
    file_path = uploads_dir / f"{uid}.{ext}"
    file_path.write_bytes(compressed)

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO uploads (uuid, session_id, batch_id, turn_timestamp, media_type, file_path, created_at)"
            " VALUES (?,?,?,NULL,?,?,?)",
            (uid, body.session_id, body.batch_id, body.media_type, str(file_path), now),
        )
        await db.commit()

    return {"uuid": uid, "url": f"/v1/uploads/{uid}"}


@router.get("/uploads/{uid}")
async def get_upload(uid: str, request: Request) -> FileResponse:
    db_path = str(request.app.state.db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT file_path, media_type FROM uploads WHERE uuid = ?", (uid,)
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Upload file missing from disk")
    return FileResponse(str(path), media_type=row["media_type"])


@router.get("/sessions/{session_id}/uploads")
async def list_session_uploads(session_id: str, request: Request) -> dict:
    """Return uploads for a session grouped by turn_timestamp (server-format string)."""
    db_path = str(request.app.state.db_path)
    await ensure_uploads_table(db_path)
    result: dict[str, list] = {}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT uuid, turn_timestamp, media_type FROM uploads"
            " WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        for row in await cursor.fetchall():
            key = row["turn_timestamp"] or "__pending__"
            result.setdefault(key, []).append({
                "uuid": row["uuid"],
                "url": f"/v1/uploads/{row['uuid']}",
                "media_type": row["media_type"],
            })
    return {"by_turn": result}
