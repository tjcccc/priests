from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from priest import PriestConfig, PriestRequest, PriestResponse, SessionRef
from priests.config.model import AppConfig
from priests.engine_factory import load_global_guide
from priests.memory.extractor import extract_memories, strip_memory_tags, write_memories, trim_memories
from priests.service.schemas import RunRequest

router = APIRouter()


def _build_priest_request(body: RunRequest, config: AppConfig, guide: str | None = None) -> PriestRequest:
    provider_options: dict = {}
    if body.no_think or not config.default.think:
        provider_options["think"] = False

    priest_config = PriestConfig(
        provider=body.provider or config.default.provider,
        model=body.model or config.default.model,
        timeout_seconds=config.default.timeout_seconds,
        max_output_tokens=body.max_output_tokens or config.default.max_output_tokens,
        provider_options=provider_options,
    )

    session_ref = None
    if body.session_id:
        session_ref = SessionRef(
            id=body.session_id,
            create_if_missing=body.create_session_if_missing,
        )

    base_context = ["Running inside priests service.", *body.system_context]
    if guide:
        base_context = [guide, *base_context]

    return PriestRequest(
        config=priest_config,
        profile=body.profile,
        prompt=body.prompt,
        session=session_ref,
        system_context=base_context,
        output=body.output,
        metadata=body.metadata,
    )


def _apply_memory(response: PriestResponse, body: RunRequest, config: AppConfig) -> PriestResponse:
    """Extract memories from response, write to disk, strip tags from text."""
    facts = extract_memories(response.text or "")
    if facts:
        memories_dir = config.paths.profiles_dir.expanduser() / body.profile / "memories"
        write_memories(memories_dir, facts)
        trim_memories(memories_dir, config.memory.limit)
    return response.model_copy(update={"text": strip_memory_tags(response.text or "")})


@router.post("/run", response_model=PriestResponse)
async def run_once(body: RunRequest, request: Request) -> PriestResponse:
    """Single run — no session required."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config
    guide = load_global_guide(config)
    priest_request = _build_priest_request(body, config, guide=guide)
    response = await engine.run(priest_request)
    if not response.ok:
        raise HTTPException(status_code=500, detail={"code": response.error.code, "message": response.error.message})
    return _apply_memory(response, body, config)


@router.post("/chat", response_model=PriestResponse)
async def chat(body: RunRequest, request: Request) -> PriestResponse:
    """Chat run — session is auto-created if session_id is not provided."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config

    if not body.session_id:
        body = body.model_copy(update={"session_id": str(uuid.uuid4()), "create_session_if_missing": True})

    guide = load_global_guide(config)
    priest_request = _build_priest_request(body, config, guide=guide)
    response = await engine.run(priest_request)
    if not response.ok:
        raise HTTPException(status_code=500, detail={"code": response.error.code, "message": response.error.message})
    return _apply_memory(response, body, config)
