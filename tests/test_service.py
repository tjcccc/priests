"""TestClient-based tests for /v1/run, /v1/chat, and SSE streaming routes."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from priest.schema.response import ExecutionInfo, PriestError, PriestResponse, SessionInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response(text: str = "Hello!", session_id: str | None = None) -> PriestResponse:
    return PriestResponse(
        text=text,
        execution=ExecutionInfo(provider="ollama", model="test", profile="default"),
        session=SessionInfo(id=session_id, is_new=True, turn_count=1) if session_id else None,
    )


def _err_response() -> PriestResponse:
    return PriestResponse(
        text=None,
        execution=ExecutionInfo(provider="ollama", model="test", profile="default"),
        error=PriestError(code="PROVIDER_ERROR", message="boom"),
    )


async def _agen(*chunks: str):
    for chunk in chunks:
        yield chunk


def _make_app():
    """Create the FastAPI app from the real config."""
    from priests.config.loader import load_config
    from priests.service.app import create_app
    return create_app(load_config())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine():
    m = MagicMock()
    m.run = AsyncMock(return_value=_ok_response("Hi!"))
    m.stream = MagicMock(return_value=_agen("Hi", "!"))
    return m


@pytest.fixture
def store():
    m = MagicMock()
    m.init = AsyncMock()
    m.close = AsyncMock()
    m.get = AsyncMock(return_value=None)
    m.save = AsyncMock()
    return m


@pytest.fixture
def client(engine, store):
    app = _make_app()
    # Patch build_engine so the lifespan injects our mocks instead of building a real engine.
    with patch("priests.service.app.build_engine", new=AsyncMock(return_value=(engine, store))):
        with TestClient(app) as c:
            yield c, engine, store


# ---------------------------------------------------------------------------
# /v1/run tests
# ---------------------------------------------------------------------------

def test_run_returns_text(client):
    c, engine, _ = client
    engine.run.return_value = _ok_response("Hello from model")
    resp = c.post("/v1/run", json={"prompt": "hi"})
    assert resp.status_code == 200
    assert resp.json()["text"] == "Hello from model"


def test_run_error_returns_500(client):
    c, engine, _ = client
    engine.run.return_value = _err_response()
    resp = c.post("/v1/run", json={"prompt": "hi"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["code"] == "PROVIDER_ERROR"


def test_run_memories_false_skips_store(client):
    c, engine, _ = client
    # Response has a session so clean_last_turn would normally be called.
    engine.run.return_value = _ok_response("ok", session_id="sess-1")
    with patch("priests.service.routes.run.clean_last_turn", new=AsyncMock()), \
         patch("priests.service.routes.run.append_memories") as mock_append, \
         patch("priests.service.routes.run.apply_memory_proposals") as mock_proposals, \
         patch("priests.service.routes.run.trim_memories") as mock_trim:
        resp = c.post("/v1/run?memories=false", json={"prompt": "hi"})
    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    assert call_request.memory == []
    mock_append.assert_not_called()
    mock_proposals.assert_not_called()
    mock_trim.assert_not_called()


def test_run_strips_memory_blocks_from_text(client):
    c, engine, _ = client
    raw = "Hello!<memory_append>[\"note\"]</memory_append> How are you?"
    engine.run.return_value = _ok_response(raw)
    resp = c.post("/v1/run?memories=false", json={"prompt": "hi"})
    assert resp.status_code == 200
    assert "<memory_append>" not in resp.json()["text"]
    assert "Hello!" in resp.json()["text"]


def test_run_forwards_images_url(client):
    c, engine, _ = client
    engine.run.return_value = _ok_response("saw image")
    resp = c.post("/v1/run", json={
        "prompt": "describe this",
        "images": [{"url": "https://example.com/img.jpg"}],
    })
    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    assert len(call_request.images) == 1
    assert call_request.images[0].url == "https://example.com/img.jpg"


def test_run_forwards_images_base64(client):
    c, engine, _ = client
    engine.run.return_value = _ok_response("saw image")
    resp = c.post("/v1/run", json={
        "prompt": "describe this",
        "images": [{"data": "abc123==", "media_type": "image/png"}],
    })
    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    assert len(call_request.images) == 1
    assert call_request.images[0].data == "abc123=="
    assert call_request.images[0].media_type == "image/png"


def test_run_uses_profile_model_override(client, tmp_path):
    c, engine, _ = client
    profile_dir = tmp_path / "coder"
    profile_dir.mkdir()
    (profile_dir / "profile.toml").write_text('provider = "bailian"\nmodel = "qwen-plus"\n')
    c.app.state.config.paths.profiles_dir = tmp_path
    engine.run.return_value = _ok_response("profile model")

    resp = c.post("/v1/run", json={"prompt": "hi", "profile": "coder"})

    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    assert call_request.config.provider == "bailian"
    assert call_request.config.model == "qwen-plus"


def test_run_explicit_model_overrides_profile_model(client, tmp_path):
    c, engine, _ = client
    profile_dir = tmp_path / "coder"
    profile_dir.mkdir()
    (profile_dir / "profile.toml").write_text('provider = "bailian"\nmodel = "qwen-plus"\n')
    c.app.state.config.paths.profiles_dir = tmp_path
    engine.run.return_value = _ok_response("explicit model")

    resp = c.post("/v1/run", json={
        "prompt": "hi",
        "profile": "coder",
        "provider": "openai",
        "model": "gpt-4.1",
    })

    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    assert call_request.config.provider == "openai"
    assert call_request.config.model == "gpt-4.1"


def test_run_assembles_profile_memory_into_request_memory(client, tmp_path):
    c, engine, _ = client
    profile_dir = tmp_path / "coder"
    memories_dir = profile_dir / "memories"
    memories_dir.mkdir(parents=True)
    (profile_dir / "PROFILE.md").write_text("profile")
    (profile_dir / "profile.toml").write_text("memories = true\n")
    (memories_dir / "user.md").write_text("User fact.")
    (memories_dir / "preferences.md").write_text("Preference fact.")
    (memories_dir / "notes.md").write_text("Legacy fact.")
    (memories_dir / "auto_short.md").write_text("# Short Memories\n\n## 2026-01-01\n\nShort fact.\n")
    c.app.state.config.paths.profiles_dir = tmp_path
    engine.run.return_value = _ok_response("memory")

    resp = c.post("/v1/run", json={"prompt": "hi", "profile": "coder"})

    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    combined = "\n".join(call_request.memory)
    assert "User fact." in combined
    assert "Preference fact." in combined
    assert "Legacy fact." in combined
    assert "Short fact." in combined
    assert any("Memory policy for priests" in ctx for ctx in call_request.context)


def test_profile_api_reads_and_writes_model_override(client, tmp_path):
    c, _, _ = client
    profile_dir = tmp_path / "coder"
    profile_dir.mkdir()
    (profile_dir / "PROFILE.md").write_text("profile")
    (profile_dir / "RULES.md").write_text("rules")
    (profile_dir / "CUSTOM.md").write_text("custom")
    (profile_dir / "profile.toml").write_text("memories = true\n")
    c.app.state.config.paths.profiles_dir = tmp_path

    resp = c.put("/v1/profiles/coder", json={
        "provider": "bailian",
        "model": "qwen-plus",
    })
    assert resp.status_code == 204

    body = c.get("/v1/profiles/coder").json()
    assert body["provider"] == "bailian"
    assert body["model"] == "qwen-plus"

    resp = c.put("/v1/profiles/coder", json={"provider": None, "model": None})
    assert resp.status_code == 204
    body = c.get("/v1/profiles/coder").json()
    assert body["provider"] is None
    assert body["model"] is None


# ---------------------------------------------------------------------------
# /v1/chat tests
# ---------------------------------------------------------------------------

def test_chat_auto_creates_session(client):
    c, engine, _ = client
    engine.run.return_value = _ok_response("hi", session_id="auto-123")
    resp = c.post("/v1/chat", json={"prompt": "hello"})
    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    assert call_request.session is not None
    assert call_request.session.create_if_missing is True


def test_chat_uses_provided_session_id(client):
    c, engine, _ = client
    engine.run.return_value = _ok_response("hi", session_id="my-session")
    resp = c.post("/v1/chat", json={"prompt": "hello", "session_id": "my-session"})
    assert resp.status_code == 200
    call_request = engine.run.call_args[0][0]
    assert call_request.session.id == "my-session"


def test_chat_error_returns_500(client):
    c, engine, _ = client
    engine.run.return_value = _err_response()
    resp = c.post("/v1/chat", json={"prompt": "hi"})
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "PROVIDER_ERROR"


# ---------------------------------------------------------------------------
# SSE /v1/run/stream tests
# ---------------------------------------------------------------------------

def test_run_stream_yields_deltas(client):
    c, engine, _ = client
    engine.stream = MagicMock(return_value=_agen("Hello", " world"))
    resp = c.post("/v1/run/stream", json={"prompt": "hi"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
    payloads = [l[len("data: "):] for l in lines]
    parsed = [json.loads(p) for p in payloads if p != "[DONE]"]
    deltas = [p["delta"] for p in parsed if "delta" in p]
    assert "".join(deltas) == "Hello world"
    assert payloads[-1] == "[DONE]"


def test_run_stream_ends_with_done(client):
    c, engine, _ = client
    engine.stream = MagicMock(return_value=_agen("ok"))
    resp = c.post("/v1/run/stream", json={"prompt": "hi"})
    assert resp.text.strip().endswith("[DONE]")


def test_run_stream_filters_memory_blocks(client):
    c, engine, _ = client
    engine.stream = MagicMock(return_value=_agen(
        "Answer.", "<memory_append>", "[\"note\"]", "</memory_append>"
    ))
    resp = c.post("/v1/run/stream?memories=false", json={"prompt": "hi"})
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
    payloads = [l[len("data: "):] for l in lines]
    parsed = [json.loads(p) for p in payloads if p != "[DONE]"]
    full_text = "".join(p["delta"] for p in parsed if "delta" in p)
    assert "<memory_append>" not in full_text
    assert "Answer." in full_text


def test_run_stream_error_yields_error_event(client):
    c, engine, _ = client

    async def _boom():
        raise RuntimeError("provider down")
        yield  # make it an async generator

    engine.stream = MagicMock(return_value=_boom())
    resp = c.post("/v1/run/stream", json={"prompt": "hi"})
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
    payloads = [l[len("data: "):] for l in lines]
    assert any("error" in p for p in payloads)
    # [DONE] must NOT appear after an error
    assert "[DONE]" not in payloads


# ---------------------------------------------------------------------------
# SSE /v1/chat/stream tests
# ---------------------------------------------------------------------------

def test_chat_stream_creates_session(client):
    c, engine, _ = client
    engine.stream = MagicMock(return_value=_agen("hey"))
    resp = c.post("/v1/chat/stream", json={"prompt": "hi"})
    assert resp.status_code == 200
    call_request = engine.stream.call_args[0][0]
    assert call_request.session is not None


def test_chat_stream_uses_provided_session(client):
    c, engine, _ = client
    engine.stream = MagicMock(return_value=_agen("hey"))
    resp = c.post("/v1/chat/stream", json={"prompt": "hi", "session_id": "sess-42"})
    assert resp.status_code == 200
    call_request = engine.stream.call_args[0][0]
    assert call_request.session.id == "sess-42"
