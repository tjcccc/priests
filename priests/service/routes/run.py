from __future__ import annotations

import base64
import json
import re
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from priest import PriestConfig, PriestRequest, PriestResponse, SessionRef
from priest import ImageInput
from priests.config.model import AppConfig
from priests.engine_factory import load_global_guide
from priests.memory.extractor import (
    StreamingStripper,
    append_memories,
    apply_consolidation,
    clean_last_turn,
    mark_consolidated,
    trim_memories,
)
from priests.profile.config import load_profile_config
from priests.service.routes.uploads import load_upload_images, save_turn_meta, update_turn_timestamps
from priests.service.schemas import RunRequest

router = APIRouter()

_APPEND_RE = re.compile(r"<memory_append>(.*?)</memory_append>", re.DOTALL | re.IGNORECASE)
_CONSOLIDATION_RE = re.compile(r"<memory_consolidation>(.*?)</memory_consolidation>", re.DOTALL | re.IGNORECASE)


def _strip_memory_blocks(text: str) -> str:
    text = _APPEND_RE.sub("", text)
    text = _CONSOLIDATION_RE.sub("", text)
    return text


def _build_images(body: RunRequest, extra: list[tuple[bytes, str]] | None = None) -> list[ImageInput]:
    images = []
    for img in body.images:
        if img.url:
            images.append(ImageInput(url=img.url, media_type=img.media_type))
        else:
            images.append(ImageInput(data=img.data, media_type=img.media_type))
    for file_bytes, media_type in (extra or []):
        images.append(ImageInput(data=base64.b64encode(file_bytes).decode(), media_type=media_type))
    return images


def _build_priest_request(
    body: RunRequest,
    config: AppConfig,
    guide: str | None = None,
    upload_images: list[tuple[bytes, str]] | None = None,
) -> PriestRequest:
    provider_options: dict = {}
    # Only forward think=True when explicitly enabled; never send think=False because
    # providers like Gemini reject unknown fields and most providers default to no thinking.
    if not body.no_think and config.default.think:
        provider_options["think"] = True

    priest_config = PriestConfig(
        provider=body.provider or config.default.provider,
        model=body.model or config.default.model,
        timeout_seconds=config.default.timeout_seconds,
        max_output_tokens=body.max_output_tokens or config.default.max_output_tokens,
        max_system_chars=body.max_system_chars,
        provider_options=provider_options,
    )

    session_ref = None
    if body.session_id:
        session_ref = SessionRef(
            id=body.session_id,
            create_if_missing=body.create_session_if_missing,
        )

    base_context = ["Running inside priests service.", *body.system_context, *body.context]
    if guide:
        base_context = [guide, *base_context]

    return PriestRequest(
        config=priest_config,
        profile=body.profile,
        prompt=body.prompt,
        session=session_ref,
        context=base_context,
        memory=body.memory,
        user_context=body.user_context,
        images=_build_images(body, upload_images),
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


def _model_label(req) -> str:
    provider = req.config.provider or "default"
    model = req.config.model or "default"
    return f"{provider}/{model}"


@router.post("/run", response_model=PriestResponse)
async def run_once(body: RunRequest, request: Request, memories: bool = True) -> PriestResponse:
    """Single run — no session required. Pass ?memories=false to disable memory loading and saving."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config
    db_path = str(request.app.state.db_path)
    guide = load_global_guide(config)
    upload_images = await load_upload_images(db_path, body.upload_uuids)
    priest_request = _build_priest_request(body, config, guide=guide, upload_images=upload_images)
    store = request.app.state.store
    t0 = time.monotonic()
    response = await engine.run(priest_request)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not response.ok:
        raise HTTPException(status_code=500, detail={"code": response.error.code, "message": response.error.message})
    if priest_request.session:
        sid = priest_request.session.id
        if body.upload_uuids:
            await update_turn_timestamps(db_path, sid, body.upload_uuids)
        await save_turn_meta(db_path, sid, _model_label(priest_request), elapsed_ms)
    return await _apply_memory(response, body, config, store, memories=memories)


@router.post("/chat", response_model=PriestResponse)
async def chat(body: RunRequest, request: Request, memories: bool = True) -> PriestResponse:
    """Chat run — session is auto-created if session_id is not provided. Pass ?memories=false to disable memory loading and saving."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config
    db_path = str(request.app.state.db_path)
    store = request.app.state.store

    if not body.session_id:
        body = body.model_copy(update={"session_id": str(uuid.uuid4()), "create_session_if_missing": True})

    guide = load_global_guide(config)
    upload_images = await load_upload_images(db_path, body.upload_uuids)
    priest_request = _build_priest_request(body, config, guide=guide, upload_images=upload_images)
    t0 = time.monotonic()
    response = await engine.run(priest_request)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not response.ok:
        raise HTTPException(status_code=500, detail={"code": response.error.code, "message": response.error.message})
    if priest_request.session:
        sid = priest_request.session.id
        if body.upload_uuids:
            await update_turn_timestamps(db_path, sid, body.upload_uuids)
        await save_turn_meta(db_path, sid, _model_label(priest_request), elapsed_ms)
    return await _apply_memory(response, body, config, store, memories=memories)


async def _sse_generator(body: RunRequest, config: AppConfig, engine, store, db_path: str, memories: bool):
    """Async generator for SSE: yields 'data: ...\n\n' lines, filters memory blocks."""
    guide = load_global_guide(config)
    upload_images = await load_upload_images(db_path, body.upload_uuids)
    priest_request = _build_priest_request(body, config, guide=guide, upload_images=upload_images)
    stripper = StreamingStripper()
    t0 = time.monotonic()

    try:
        async for chunk in engine.stream(priest_request):
            safe = stripper.feed(chunk)
            if safe:
                yield f"data: {json.dumps({'delta': safe})}\n\n"
        tail = stripper.flush()
        if tail:
            yield f"data: {json.dumps({'delta': tail})}\n\n"
    except Exception as exc:
        code = getattr(exc, "code", "UNKNOWN_ERROR")
        msg = getattr(exc, "message", str(exc))
        yield f"data: {json.dumps({'error': {'code': code, 'message': msg}})}\n\n"
        return

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Post-stream: update upload turn timestamps, then handle memory
    if priest_request.session:
        sid = priest_request.session.id
        if body.upload_uuids:
            await update_turn_timestamps(db_path, sid, body.upload_uuids)
        await clean_last_turn(store, sid)
        await save_turn_meta(db_path, sid, _model_label(priest_request), elapsed_ms)

    if memories:
        profile_cfg = load_profile_config(config.paths.profiles_dir, body.profile)
        if profile_cfg.memories:
            size_limit = (
                profile_cfg.memories_limit
                if profile_cfg.memories_limit is not None
                else config.memory.size_limit
            )
            memories_dir = config.paths.profiles_dir.expanduser() / body.profile / "memories"
            if stripper.consolidation_json:
                try:
                    apply_consolidation(memories_dir, json.loads(stripper.consolidation_json))
                    mark_consolidated(memories_dir)
                except (json.JSONDecodeError, ValueError):
                    pass
            if stripper.append_json:
                try:
                    append_memories(memories_dir, json.loads(stripper.append_json))
                except (json.JSONDecodeError, ValueError):
                    pass
            trim_memories(memories_dir, size_limit)

    provider = priest_request.config.provider or "default"
    model = priest_request.config.model or "default"
    yield f"data: {json.dumps({'metadata': {'model': f'{provider}/{model}'}})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/run/stream")
async def run_once_stream(body: RunRequest, request: Request, memories: bool = True) -> StreamingResponse:
    """Single run with SSE streaming. Each chunk: data: {"delta": "..."}.  Final event: data: [DONE]."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config
    store = request.app.state.store
    db_path = str(request.app.state.db_path)
    return StreamingResponse(
        _sse_generator(body, config, engine, store, db_path, memories),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/stream")
async def chat_stream(body: RunRequest, request: Request, memories: bool = True) -> StreamingResponse:
    """Chat run with SSE streaming. Session is auto-created if session_id is not provided."""
    engine = request.app.state.engine
    config: AppConfig = request.app.state.config
    store = request.app.state.store
    db_path = str(request.app.state.db_path)

    if not body.session_id:
        body = body.model_copy(update={"session_id": str(uuid.uuid4()), "create_session_if_missing": True})

    return StreamingResponse(
        _sse_generator(body, config, engine, store, db_path, memories),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
