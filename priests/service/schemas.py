from __future__ import annotations

from datetime import datetime

from priest.schema.request import OutputSpec
from pydantic import BaseModel


class RunRequest(BaseModel):
    prompt: str
    provider: str | None = None
    model: str | None = None
    profile: str = "default"
    session_id: str | None = None
    create_session_if_missing: bool = True
    system_context: list[str] = []
    no_think: bool = False
    max_output_tokens: int | None = None
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
