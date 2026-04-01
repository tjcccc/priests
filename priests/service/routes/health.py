from __future__ import annotations

from fastapi import APIRouter

from priests import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}
