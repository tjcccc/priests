"""Tests for GET /v1/config and PATCH /v1/config routes."""
from __future__ import annotations

import tomllib
from zipfile import ZipFile
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
    for expected in (
        "ollama", "llamacpp", "rapidmlx", "openai", "anthropic", "mistral", "perplexity", "cohere"
    ):
        assert expected in registry_names


def test_local_openai_compat_providers_have_defaults():
    from priests.config.model import AppConfig
    from priests.engine_factory import build_adapters

    config = AppConfig()

    assert config.providers.llamacpp.base_url == "http://localhost:8080"
    assert config.providers.rapidmlx.base_url == "http://localhost:8000/v1"

    adapters = build_adapters(config)
    assert adapters["llamacpp"].provider_name == "llamacpp"
    assert adapters["rapidmlx"].provider_name == "rapidmlx"


def test_openai_models_url_handles_versioned_and_unversioned_base_urls():
    from priests.cli.init_cmd import _openai_models_url

    assert _openai_models_url("http://localhost:8000/v1") == "http://localhost:8000/v1/models"
    assert _openai_models_url("http://localhost:8080") == "http://localhost:8080/v1/models"


def test_default_service_port_is_9000():
    from priests.config.model import AppConfig

    assert AppConfig().service.port == 9000


def test_oauth_registry_lists_current_frontier_models():
    from priests.registry import REGISTRY

    copilot_models = set(REGISTRY["github_copilot"].known_models or [])
    chatgpt_models = set(REGISTRY["chatgpt"].known_models or [])

    assert "gpt-5.5" in copilot_models
    assert "gpt-5.4-nano" in copilot_models
    assert "claude-sonnet-4.6" in copilot_models
    assert "gemini-3.1-pro" in copilot_models
    assert "o3-mini" not in copilot_models

    assert "gpt-5.5" in chatgpt_models
    assert "gpt-5.4-mini" in chatgpt_models
    assert "o4-mini" not in chatgpt_models


def test_github_copilot_adapter_sends_ide_headers():
    from priests.config.model import AppConfig, OpenAICompatConfig
    from priests.engine_factory import build_adapters

    config = AppConfig()
    config.providers.github_copilot = OpenAICompatConfig(
        base_url="https://api.githubcopilot.com",
        api_key="tid=test",
    )

    adapter = build_adapters(config)["github_copilot"]

    assert adapter.provider_name == "github_copilot"
    assert adapter._headers["Editor-Version"] == "priests/0"


def test_oauth_models_url_handles_github_copilot_without_v1():
    from priests.service.routes.config import _oauth_models_url

    assert _oauth_models_url("github_copilot", "https://api.githubcopilot.com") == (
        "https://api.githubcopilot.com/models"
    )
    assert _oauth_models_url("chatgpt", "https://api.openai.com/v1") == "https://api.openai.com/v1/models"


def test_github_copilot_device_start_returns_user_code(client):
    from priests.service.routes import config as config_route

    c, _ = client

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "device_code": "device-123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    with patch.object(config_route.httpx, "AsyncClient", FakeAsyncClient):
        resp = c.post("/v1/providers/github_copilot/device/start")

    assert resp.status_code == 200
    assert resp.json()["user_code"] == "ABCD-1234"


def test_github_copilot_device_poll_saves_copilot_token(client):
    from priests.service.routes import config as config_route

    c, engine = client

    class FakeResponse:
        def __init__(self, data):
            self.status_code = 200
            self._data = data
            self.text = ""

        def json(self):
            return self._data

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse({"access_token": "gho-test"})

        async def get(self, *args, **kwargs):
            return FakeResponse({
                "token": "tid=test",
                "expires_at": 1893456000,
                "endpoints": {"api": "https://api.githubcopilot.com"},
            })

    with patch.object(config_route.httpx, "AsyncClient", FakeAsyncClient), \
         patch.object(config_route, "load_config", return_value=c.app.state.config), \
         patch.object(config_route, "save_config") as save:
        resp = c.post("/v1/providers/github_copilot/device/poll", json={"device_code": "device-123"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "authorized"
    assert c.app.state.config.providers.github_copilot.api_key == "tid=test"
    assert c.app.state.config.providers.github_copilot.oauth_token == "gho-test"
    assert c.app.state.config.providers.github_copilot.api_key_expires_at == 1893456000
    assert "github_copilot" in engine._adapters
    save.assert_called_once()


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


def test_put_model_options_rejects_unknown_provider(client):
    c, _ = client
    resp = c.put("/v1/config/models/options", json={"options": ["github_copilit/gpt-5-mini"]})
    assert resp.status_code == 422
    assert "Unknown provider" in resp.json()["detail"]


def test_provider_status_endpoint_uses_status_helper(client):
    from priests.provider_status import ProviderStatus
    from priests.service.routes import config as config_route

    c, _ = client

    async def fake_status(_config, name, timeout=2.0):
        return ProviderStatus(
            name=name,
            label=f"{name} label",
            provider_type="local" if name == "ollama" else "api",
            configured=True,
            reachable=True if name == "ollama" else None,
            base_url="http://example.test",
            model_count=1 if name == "ollama" else None,
            models=["test"],
            message="ok",
        )

    with patch.object(config_route, "provider_status_async", fake_status):
        resp = c.get("/v1/providers/status")

    assert resp.status_code == 200
    body = resp.json()
    ollama = next(row for row in body if row["name"] == "ollama")
    assert ollama["reachable"] is True
    assert ollama["model_count"] == 1


def test_provider_validate_endpoint_uses_validation_helper(client):
    from priests.provider_status import ModelValidation
    from priests.service.routes import config as config_route

    c, _ = client
    with patch.object(
        config_route,
        "validate_model",
        return_value=ModelValidation("ollama", "llama3", True, "ok", "Model is available locally"),
    ):
        resp = c.post("/v1/providers/validate", json={"provider": "ollama", "model": "llama3"})

    assert resp.status_code == 200
    assert resp.json() == {
        "provider": "ollama",
        "model": "llama3",
        "valid": True,
        "status": "ok",
        "message": "Model is available locally",
    }


def test_config_export_strips_secrets_and_includes_profiles(tmp_path):
    from priests.cli.config_cmd import config_export

    profiles = tmp_path / "profiles"
    profile = profiles / "default"
    profile.mkdir(parents=True)
    (profile / "PROFILE.md").write_text("# Default\n")
    config_path = tmp_path / "priests.toml"
    config_path.write_text(
        f"""
[default]
provider = "ollama"
model = "llama3"
profile = "default"

[paths]
profiles_dir = "{profiles}"

[providers.openai]
api_key = "sk-secret"
base_url = "https://api.openai.com/v1"
""".strip()
    )
    archive = tmp_path / "export.zip"

    config_export(output=archive, config_file=config_path)

    with ZipFile(archive) as zf:
        exported_config = zf.read("config/priests.toml").decode()
        assert "sk-secret" not in exported_config
        assert 'api_key = ""' in exported_config
        assert "profiles/default/PROFILE.md" in zf.namelist()


def test_config_import_restores_config_and_profiles(tmp_path):
    from priests.cli.config_cmd import config_export, config_import

    src_profiles = tmp_path / "src_profiles"
    src_profile = src_profiles / "coder"
    src_profile.mkdir(parents=True)
    (src_profile / "PROFILE.md").write_text("# Coder\n")
    src_config = tmp_path / "src.toml"
    src_config.write_text(
        f"""
[default]
provider = "ollama"
model = "llama3"
profile = "coder"

[paths]
profiles_dir = "{src_profiles}"
""".strip()
    )
    archive = tmp_path / "export.zip"
    config_export(output=archive, config_file=src_config)

    dst_config = tmp_path / "dst.toml"
    dst_profiles = tmp_path / "dst_profiles"
    config_import(
        archive=archive,
        overwrite=True,
        profiles_dir=dst_profiles,
        config_file=dst_config,
    )

    data = tomllib.loads(dst_config.read_text())
    assert data["default"]["profile"] == "coder"
    assert (dst_profiles / "coder" / "PROFILE.md").read_text() == "# Coder\n"
