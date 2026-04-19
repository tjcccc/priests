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
    c, engine, store = client
    engine.run.return_value = _ok_response("ok")
    resp = c.post("/v1/run?memories=false", json={"prompt": "hi"})
    assert resp.status_code == 200
    store.save.assert_not_called()


def test_run_forwards_images(client):
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


# ---------------------------------------------------------------------------
# /v1/chat tests
# ---------------------------------------------------------------------------

def test_chat_auto_creates_session(client):
    c, engine, _ = client
    engine.run.return_value = _ok_response("hi", session_id="auto-123")
    resp = c.post("/v1/chat", json={"prompt": "hello"})
    assert resp.status_code == 200
    # Engine was called with a session ref
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
    deltas = [json.loads(p)["delta"] for p in payloads if p != "[DONE]"]
    assert "".join(deltas) == "Hello world"
    assert payloads[-1] == "[DONE]"


def test_run_stream_ends_with_done(client):
    c, engine, _ = client
    engine.stream = MagicMock(return_value=_agen("ok"))
    resp = c.post("/v1/run/stream", json={"prompt": "hi"})
    assert resp.text.strip().endswith("[DONE]")


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
