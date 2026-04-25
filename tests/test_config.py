"""Tests for GET /v1/config and PATCH /v1/config routes."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_app():
    from priests.config.loader import load_config
    from priests.service.app import create_app
    return create_app(load_config())


@pytest.fixture
def engine():
    m = MagicMock()
    m._adapters = {}
    m.run = AsyncMock()
    m.stream = MagicMock(return_value=iter([]))
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
    with patch("priests.service.app.build_engine", new=AsyncMock(return_value=(engine, store))):
        with TestClient(app) as c:
            yield c, engine


# ---------------------------------------------------------------------------
# GET /v1/config
# ---------------------------------------------------------------------------

def test_get_config_shape(client):
    c, _ = client
    resp = c.get("/v1/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "defaults" in body
    assert "providers" in body
    assert "memory" in body
    assert "web_search" in body
    assert "service" in body
    assert "paths" in body
    assert "registry" in body


def test_get_config_api_keys_masked(client):
    """Real API key values must never appear in the response."""
    c, _ = client
    body = c.get("/v1/config").json()
    for name, prov in body["providers"].items():
        raw_key = prov.get("api_key", "")
        # The only allowed non-empty values are the masked sentinel
        assert raw_key in ("", "••••••"), (
            f"Provider {name!r} leaked a real API key: {raw_key!r}"
        )


def test_get_config_registry_has_known_providers(client):
    c, _ = client
    body = c.get("/v1/config").json()
    registry_names = {r["name"] for r in body["registry"]}
    for expected in ("ollama", "openai", "anthropic", "mistral", "perplexity", "cohere"):
        assert expected in registry_names


# ---------------------------------------------------------------------------
# PATCH /v1/config
# ---------------------------------------------------------------------------

def test_patch_non_restart_key(client):
    c, _ = client
    resp = c.patch("/v1/config", json={"updates": {"default.profile": "_test_"}})
    assert resp.status_code == 200
    assert resp.json()["needs_restart"] is False
    # Change is reflected in a follow-up GET
    body = c.get("/v1/config").json()
    assert body["defaults"]["profile"] == "_test_"


def test_patch_restart_key(client):
    c, _ = client
    resp = c.patch("/v1/config", json={"updates": {"service.port": "9000"}})
    assert resp.status_code == 200
    assert resp.json()["needs_restart"] is True


def test_patch_none_provider_no_error(client):
    """Setting a key on a None provider (e.g. anthropic not configured) must not 500."""
    c, _ = client
    # First confirm anthropic has no key set (may vary by local config, but should not 500)
    resp = c.patch("/v1/config", json={"updates": {"providers.anthropic.api_key": "sk-test"}})
    assert resp.status_code == 200
    # Key must be masked on GET
    body = c.get("/v1/config").json()
    assert body["providers"]["anthropic"]["api_key"] == "••••••"


def test_patch_invalid_int_returns_422(client):
    c, _ = client
    resp = c.patch("/v1/config", json={"updates": {"service.port": "not_a_number"}})
    assert resp.status_code == 422
