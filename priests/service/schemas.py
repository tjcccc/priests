from __future__ import annotations

from datetime import datetime

from priest.schema.request import OutputSpec
from pydantic import BaseModel, model_validator


class ImageIn(BaseModel):
    """Image attachment for the HTTP API. Accepts url or base64 data (no local paths)."""
    url: str | None = None
    data: str | None = None  # base64-encoded bytes
    media_type: str = "image/jpeg"

    @model_validator(mode="after")
    def _check_source(self) -> "ImageIn":
        if not self.url and not self.data:
            raise ValueError("ImageIn requires url or data")
        if self.url and self.data:
            raise ValueError("ImageIn: provide url or data, not both")
        return self


class RunRequest(BaseModel):
    prompt: str
    provider: str | None = None
    model: str | None = None
    profile: str = "default"
    session_id: str | None = None
    create_session_if_missing: bool = True
    # Legacy alias for `context` — kept for HTTP API backward compatibility.
    # New clients should use `context` instead.
    system_context: list[str] = []
    # priest-core v2 fields
    context: list[str] = []
    memory: list[str] = []
    user_context: list[str] = []
    max_system_chars: int | None = None
    no_think: bool = False
    max_output_tokens: int | None = None
    images: list[ImageIn] = []
    upload_uuids: list[str] = []
    output: OutputSpec = OutputSpec()
    metadata: dict = {}


class TurnOut(BaseModel):
    role: str
    content: str
    timestamp: datetime


class SessionSummary(BaseModel):
    id: str
    profile_name: str
    created_at: datetime
    updated_at: datetime
    turn_count: int


class SessionDetail(BaseModel):
    id: str
    profile_name: str
    created_at: datetime
    updated_at: datetime
    turns: list[TurnOut]

    @classmethod
    def from_session(cls, session) -> SessionDetail:
        return cls(
            id=session.id,
            profile_name=session.profile_name,
            created_at=session.created_at,
            updated_at=session.updated_at,
            turns=[
                TurnOut(role=t.role, content=t.content, timestamp=t.timestamp)
                for t in session.turns
            ],
        )
