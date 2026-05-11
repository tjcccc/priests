from __future__ import annotations

import base64
import json
import re
import time
import uuid

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from priest import PriestConfig, PriestRequest, PriestResponse, SessionRef
from priest import ImageInput
from priests.config.loader import load_config, save_config
from priests.config.model import AppConfig, OpenAICompatConfig
from priests.engine_factory import build_adapters, load_global_guide
from priests.memory.extractor import (
    StreamingStripper,
    append_memories,
    apply_memory_forget,
    apply_memory_proposals,
    assemble_memory_entries,
    build_memory_instructions,
    clean_last_turn,
    forget_prompt_memories,
    save_memories,
    save_prompt_memories,
    should_inject_memory_instructions,
    trim_memories,
)
from priests.profile.config import load_profile_config, resolve_provider_model
from priests.providers.github_copilot_auth import (
    GitHubCopilotAuthError,
    exchange_github_token_for_copilot_token,
    looks_like_copilot_ide_token,
)
from priests.providers.chatgpt_auth import (
    ChatGPTOAuthError,
    refresh_chatgpt_access_token,
)
from priests.registry import REGISTRY
from priests.service.routes.uploads import load_upload_images, save_turn_meta, update_turn_timestamps
from priests.service.schemas import RunRequest

router = APIRouter()

_SAVE_RE = re.compile(r"<memory_save>(.*?)</memory_save>", re.DOTALL | re.IGNORECASE)
_APPEND_RE = re.compile(r"<memory_append>(.*?)</memory_append>", re.DOTALL | re.IGNORECASE)
_PROPOSAL_RE = re.compile(r"<memory_proposal>(.*?)</memory_proposal>", re.DOTALL | re.IGNORECASE)
_FORGET_RE = re.compile(r"<memory_forget>(.*?)</memory_forget>", re.DOTALL | re.IGNORECASE)
_CONSOLIDATION_RE = re.compile(r"<memory_consolidation>(.*?)</memory_consolidation>", re.DOTALL | re.IGNORECASE)
_COPILOT_REFRESH_SKEW_SECONDS = 300


def _strip_memory_blocks(text: str) -> str:
    stripper = StreamingStripper()
    return stripper.feed(text) + stripper.flush()


def _json_payloads(*payloads: str | None):
    for payload_text in payloads:
        if not payload_text:
            continue
        try:
            yield json.loads(payload_text)
        except (json.JSONDecodeError, ValueError):
            continue


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
    memories: bool = True,
) -> PriestRequest:
    provider_options: dict = {}
    # Only forward think=True when explicitly enabled; never send think=False because
    # providers like Gemini reject unknown fields and most providers default to no thinking.
    thinking_enabled = not body.no_think and config.default.think
    if thinking_enabled:
        provider_options["think"] = True

    resolved_provider, resolved_model = resolve_provider_model(config, body.profile, body.provider, body.model)
    priest_config = PriestConfig(
        provider=resolved_provider,
        model=resolved_model,
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

    profile_cfg = load_profile_config(config.paths.profiles_dir, body.profile)
    memories_enabled = memories and profile_cfg.memories
    request_memory = list(body.memory)
    if memories_enabled:
        memories_dir = config.paths.profiles_dir.expanduser() / body.profile / "memories"
        if should_inject_memory_instructions(body.prompt):
            base_context.append(build_memory_instructions())
        request_memory.extend(
            assemble_memory_entries(
                memories_dir,
                config.memory.context_limit,
                thinking=thinking_enabled,
                prompt=body.prompt,
            )
        )
    else:
        request_memory = []

    return PriestRequest(
        config=priest_config,
        profile=body.profile,
        prompt=body.prompt,
        session=session_ref,
        context=base_context,
        memory=request_memory,
        user_context=body.user_context,
        images=_build_images(body, upload_images),
        output=body.output,
        metadata=body.metadata,
    )


def _provider_config_error(config: AppConfig, provider: str | None) -> str | None:
    """Return a user-actionable configuration error for a missing provider adapter."""
    if not provider:
        return "No provider is selected. Set a default provider/model or pass a provider explicitly."

    info = REGISTRY.get(provider)
    if info is None:
        return f"Unknown provider {provider!r}. Add a provider from Configuration > Model Configuration first."

    if info.provider_type == "local":
        return None

    cfg = getattr(config.providers, provider, None)
    if cfg is None:
        return (
            f"Provider {provider!r} is not configured. "
            "Add its token/API key in Configuration > Providers before using this model."
        )

    if info.needs_api_key and not getattr(cfg, "api_key", ""):
        return (
            f"Provider {provider!r} is missing a token/API key. "
            "Add it in Configuration > Providers before using this model."
        )

    if provider != "anthropic" and not getattr(cfg, "base_url", ""):
        return f"Provider {provider!r} is missing a base URL in Configuration > Providers."

    return None


def _registered_provider_error(engine, config: AppConfig, provider: str | None) -> str | None:
    """Validate against the live engine adapter registry when it is available."""
    adapters = getattr(engine, "_adapters", None)
    if not isinstance(adapters, dict) or not provider or provider in adapters:
        return None
    return _provider_config_error(config, provider) or (
        f"Provider {provider!r} is configured but is not registered in the running engine. "
        "Save it again in Configuration > Providers or restart the priests service."
    )


async def _refresh_github_copilot_if_needed(request: Request, provider: str | None) -> str | None:
    if provider != "github_copilot":
        return None

    config: AppConfig = request.app.state.config
    cfg = config.providers.github_copilot
    if not cfg or (not cfg.api_key and not cfg.oauth_token):
        return None

    now = int(time.time())
    github_token = cfg.oauth_token
    should_refresh = bool(
        github_token
        and (
            not cfg.api_key
            or cfg.api_key_expires_at is None
            or cfg.api_key_expires_at <= now + _COPILOT_REFRESH_SKEW_SECONDS
        )
    )

    if not should_refresh and cfg.api_key and not looks_like_copilot_ide_token(cfg.api_key):
        github_token = cfg.api_key
        should_refresh = True

    if not should_refresh:
        return None

    try:
        refreshed = await exchange_github_token_for_copilot_token(github_token)
    except GitHubCopilotAuthError as exc:
        return (
            f"GitHub Copilot authorization could not be refreshed: {exc}. "
            "Authorize GitHub Copilot again in Configuration > Providers."
        )

    current = load_config()
    existing = current.providers.github_copilot or cfg
    current.providers.github_copilot = OpenAICompatConfig(
        api_key=refreshed.token,
        base_url=refreshed.base_url,
        use_proxy=existing.use_proxy,
        oauth_token=github_token,
        api_key_expires_at=refreshed.expires_at,
    )
    save_config(current)
    request.app.state.config = current
    request.app.state.engine._adapters = build_adapters(current)
    return None


async def _refresh_chatgpt_if_needed(request: Request, provider: str | None) -> str | None:
    if provider != "chatgpt":
        return None

    config: AppConfig = request.app.state.config
    cfg = config.providers.chatgpt
    if not cfg or not cfg.oauth_token:
        return None

    now = int(time.time())
    should_refresh = not cfg.api_key or cfg.api_key_expires_at is None or cfg.api_key_expires_at <= now + 60
    if not should_refresh:
        return None

    try:
        refreshed = await anyio.to_thread.run_sync(refresh_chatgpt_access_token, cfg.oauth_token)
    except ChatGPTOAuthError as exc:
        return (
            f"ChatGPT authorization could not be refreshed: {exc}. "
            "Sign in with ChatGPT again in Configuration > Providers."
        )

    current = load_config()
    existing = current.providers.chatgpt or cfg
    current.providers.chatgpt = OpenAICompatConfig.model_validate(
        {
            "api_key": refreshed.api_key or existing.api_key or cfg.api_key or refreshed.access_token,
            "base_url": existing.base_url or cfg.base_url,
            "use_proxy": existing.use_proxy,
            "oauth_token": refreshed.refresh_token,
            "api_key_expires_at": refreshed.expires_at,
        }
    )
    save_config(current)
    request.app.state.config = current
    request.app.state.engine._adapters = build_adapters(current)
    return None


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
    stripper = StreamingStripper()
    visible_text = stripper.feed(text) + stripper.flush()
    if memories:
        profile_cfg = load_profile_config(config.paths.profiles_dir, body.profile)
        if profile_cfg.memories:
            size_limit = (
                profile_cfg.memories_limit
                if profile_cfg.memories_limit is not None
                else config.memory.size_limit
            )
            memories_dir = config.paths.profiles_dir.expanduser() / body.profile / "memories"
            session_id = response.session.id if response.session else body.session_id
            for payload in _json_payloads(*stripper.save_jsons):
                save_memories(memories_dir, payload, session_id=session_id)
            for payload in _json_payloads(*stripper.append_jsons):
                append_memories(memories_dir, payload, session_id=session_id)
            for payload in _json_payloads(*stripper.proposal_jsons):
                apply_memory_proposals(memories_dir, payload, session_id=session_id)
            for payload in _json_payloads(*stripper.forget_jsons):
                apply_memory_forget(memories_dir, payload, session_id=session_id)
            forget_prompt_memories(
                memories_dir,
                body.prompt,
                session_id=response.session.id if response.session else body.session_id,
            )
            save_prompt_memories(
                memories_dir,
                body.prompt,
                session_id=response.session.id if response.session else body.session_id,
            )
            trim_memories(memories_dir, size_limit)
    return response.model_copy(update={"text": visible_text})


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
    priest_request = _build_priest_request(body, config, guide=guide, upload_images=upload_images, memories=memories)
    if message := await _refresh_github_copilot_if_needed(request, priest_request.config.provider):
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_AUTH_EXPIRED", "message": message})
    if message := await _refresh_chatgpt_if_needed(request, priest_request.config.provider):
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_AUTH_EXPIRED", "message": message})
    config = request.app.state.config
    if message := _registered_provider_error(engine, config, priest_request.config.provider):
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_NOT_CONFIGURED", "message": message})
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
    priest_request = _build_priest_request(body, config, guide=guide, upload_images=upload_images, memories=memories)
    if message := await _refresh_github_copilot_if_needed(request, priest_request.config.provider):
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_AUTH_EXPIRED", "message": message})
    if message := await _refresh_chatgpt_if_needed(request, priest_request.config.provider):
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_AUTH_EXPIRED", "message": message})
    config = request.app.state.config
    if message := _registered_provider_error(engine, config, priest_request.config.provider):
        raise HTTPException(status_code=400, detail={"code": "PROVIDER_NOT_CONFIGURED", "message": message})
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


async def _sse_generator(body: RunRequest, request: Request, memories: bool):
    """Async generator for SSE: yields 'data: ...\n\n' lines, filters memory blocks."""
    config: AppConfig = request.app.state.config
    engine = request.app.state.engine
    store = request.app.state.store
    db_path = str(request.app.state.db_path)
    guide = load_global_guide(config)
    upload_images = await load_upload_images(db_path, body.upload_uuids)
    priest_request = _build_priest_request(body, config, guide=guide, upload_images=upload_images, memories=memories)
    if message := await _refresh_github_copilot_if_needed(request, priest_request.config.provider):
        yield f"data: {json.dumps({'error': {'code': 'PROVIDER_AUTH_EXPIRED', 'message': message}})}\n\n"
        return
    if message := await _refresh_chatgpt_if_needed(request, priest_request.config.provider):
        yield f"data: {json.dumps({'error': {'code': 'PROVIDER_AUTH_EXPIRED', 'message': message}})}\n\n"
        return
    config = request.app.state.config
    if message := _registered_provider_error(engine, config, priest_request.config.provider):
        yield f"data: {json.dumps({'error': {'code': 'PROVIDER_NOT_CONFIGURED', 'message': message}})}\n\n"
        return
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
            for payload in _json_payloads(*stripper.save_jsons):
                save_memories(memories_dir, payload, session_id=body.session_id)
            for payload in _json_payloads(*stripper.append_jsons):
                append_memories(memories_dir, payload, session_id=body.session_id)
            for payload in _json_payloads(*stripper.proposal_jsons):
                apply_memory_proposals(memories_dir, payload, session_id=body.session_id)
            for payload in _json_payloads(*stripper.forget_jsons):
                apply_memory_forget(memories_dir, payload, session_id=body.session_id)
            forget_prompt_memories(memories_dir, body.prompt, session_id=body.session_id)
            save_prompt_memories(memories_dir, body.prompt, session_id=body.session_id)
            trim_memories(memories_dir, size_limit)

    provider = priest_request.config.provider or "default"
    model = priest_request.config.model or "default"
    yield f"data: {json.dumps({'metadata': {'model': f'{provider}/{model}'}})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/run/stream")
async def run_once_stream(body: RunRequest, request: Request, memories: bool = True) -> StreamingResponse:
    """Single run with SSE streaming. Each chunk: data: {"delta": "..."}.  Final event: data: [DONE]."""
    return StreamingResponse(
        _sse_generator(body, request, memories),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/stream")
async def chat_stream(body: RunRequest, request: Request, memories: bool = True) -> StreamingResponse:
    """Chat run with SSE streaming. Session is auto-created if session_id is not provided."""
    if not body.session_id:
        body = body.model_copy(update={"session_id": str(uuid.uuid4()), "create_session_if_missing": True})

    return StreamingResponse(
        _sse_generator(body, request, memories),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
