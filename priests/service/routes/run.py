from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, HTTPException, Request

from priest import PriestConfig, PriestRequest, PriestResponse, SessionRef
from priests.config.model import AppConfig
from priests.engine_factory import load_global_guide
from priests.memory.extractor import (
    append_memories,
    apply_consolidation,
    clean_last_turn,
    mark_consolidated,
    trim_memories,
)
from priests.profile.config import load_profile_config
from priests.service.schemas import RunRequest

router = APIRouter()

_APPEND_RE = re.compile(r"<memory_append>(.*?)</memory_append>", re.DOTALL | re.IGNORECASE)
_CONSOLIDATION_RE = re.compile(r"<memory_consolidation>(.*?)</memory_consolidation>", re.DOTALL | re.IGNORECASE)


def _strip_memory_blocks(text: str) -> str:
    text = _APPEND_RE.sub("", text)
    text = _CONSOLIDATION_RE.sub("", text)
    return text


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


async def _apply_memory(
    response: PriestResponse,
    body: RunRequest,
    config: AppConfig,
    store,
    memories: bool = True,
) -> PriestResponse:
    """Strip memory blocks from session store, persist to disk, and strip blocks from response text."""
    if response.session:
        await clean_last_turn(store, response.session.id)
    text = response.text or ""
    if memories:
        profile_cfg = load_profile_config(config.paths.profiles_dir, body.profile)
        if profile_cfg.memories:
            size_limit = (
                profile_cfg.memories_limit
                if profile_cfg.memories_limit is not None
                else config.memory.size_limit
            )
            memories_dir = config.paths.profiles_dir.expanduser() / body.profile / "memories"
            if m := _CONSOLIDATION_RE.search(text):
                try:
                    apply_consolidation(memories_dir, json.loads(m.group(1).strip()))
                    mark_consolidated(memories_dir)
                except (json.JSONDecodeError, ValueError):
                    pass
            if m := _APPEND_RE.search(text):
                try:
                    append_memories(memories_dir, json.loads(m.group(1).strip()))
                except (json.JSONDecodeError, ValueError):
                    pass
            trim_memories(memories_dir, size_limit)
    return response.model_copy(update={"text": _strip_memory_blocks(text)})


@router.post("/run", response_model=PriestResponse)
async def run_once(body: RunRequest, request: Request, memories: bool = True) -> PriestResponse:
    """Single run — no session required. Pass ?memories=false to disable memory loading and saving."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config
    guide = load_global_guide(config)
    priest_request = _build_priest_request(body, config, guide=guide)
    store = request.app.state.store
    response = await engine.run(priest_request)
    if not response.ok:
        raise HTTPException(status_code=500, detail={"code": response.error.code, "message": response.error.message})
    return await _apply_memory(response, body, config, store, memories=memories)


@router.post("/chat", response_model=PriestResponse)
async def chat(body: RunRequest, request: Request, memories: bool = True) -> PriestResponse:
    """Chat run — session is auto-created if session_id is not provided. Pass ?memories=false to disable memory loading and saving."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config
    store = request.app.state.store

    if not body.session_id:
        body = body.model_copy(update={"session_id": str(uuid.uuid4()), "create_session_if_missing": True})

    guide = load_global_guide(config)
    priest_request = _build_priest_request(body, config, guide=guide)
    response = await engine.run(priest_request)
    if not response.ok:
        raise HTTPException(status_code=500, detail={"code": response.error.code, "message": response.error.message})
    return await _apply_memory(response, body, config, store, memories=memories)
