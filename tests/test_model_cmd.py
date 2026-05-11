from __future__ import annotations

import tomllib

import pytest
import typer


def _write_config(path, profiles_dir, options: list[str] | None = None) -> None:
    opts = options or ["bailian/qwen-plus", "openrouter/openai/gpt-4.1"]
    rendered_opts = ", ".join(repr(o) for o in opts)
    path.write_text(
        f"""
[default]
provider = "ollama"
model = "llama3"
profile = "default"

[models]
options = [{rendered_opts}]

[paths]
profiles_dir = "{profiles_dir}"
""".strip()
    )


def test_model_default_profile_sets_profile_model(tmp_path, monkeypatch):
    from priests.cli import model_cmd

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profile_dir = profiles_dir / "coder"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text("memories = true\n")
    _write_config(config_path, profiles_dir)

    monkeypatch.setattr(model_cmd, "_arrow_select", lambda _prompt, _choices: "bailian/qwen-plus")

    model_cmd.model_default(profile="coder", config_file=config_path)

    data = tomllib.loads((profile_dir / "profile.toml").read_text())
    assert data["provider"] == "bailian"
    assert data["model"] == "qwen-plus"


def test_model_default_profile_use_default_clears_profile_model(tmp_path, monkeypatch):
    from priests.cli import model_cmd

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profile_dir = profiles_dir / "coder"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text(
        'memories = true\nprovider = "bailian"\nmodel = "qwen-plus"\n'
    )
    _write_config(config_path, profiles_dir)

    def fake_select(_prompt, choices):
        assert choices[0].value == model_cmd._USE_GLOBAL_DEFAULT
        assert "Use default (ollama/llama3)" in choices[0].title
        return model_cmd._USE_GLOBAL_DEFAULT

    monkeypatch.setattr(model_cmd, "_arrow_select", fake_select)

    model_cmd.model_default(profile="coder", config_file=config_path)

    data = tomllib.loads((profile_dir / "profile.toml").read_text())
    assert "provider" not in data
    assert "model" not in data
    assert data["memories"] is True


def test_model_default_profile_invalid_name_exits(tmp_path):
    from priests.cli import model_cmd

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    _write_config(config_path, profiles_dir)

    with pytest.raises(typer.Exit) as exc:
        model_cmd.model_default(profile="missing", config_file=config_path)

    assert exc.value.exit_code == 1


def test_model_validate_uses_default_model(tmp_path, monkeypatch):
    from priests.cli import model_cmd
    from priests.provider_status import ModelValidation

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    _write_config(config_path, profiles_dir, options=["ollama/llama3"])

    seen = {}

    def fake_validate(_config, provider, model):
        seen["provider"] = provider
        seen["model"] = model
        return ModelValidation(provider, model, True, "ok", "valid")

    monkeypatch.setattr(model_cmd, "validate_model", fake_validate)

    model_cmd.model_validate(config_file=config_path)

    assert seen == {"provider": "ollama", "model": "llama3"}


def test_model_validate_invalid_pair_exits(tmp_path, monkeypatch):
    from priests.cli import model_cmd
    from priests.provider_status import ModelValidation

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    _write_config(config_path, profiles_dir)

    monkeypatch.setattr(
        model_cmd,
        "validate_model",
        lambda _config, provider, model: ModelValidation(provider, model, False, "error", "missing"),
    )

    with pytest.raises(typer.Exit) as exc:
        model_cmd.model_validate(model="ollama/missing", config_file=config_path)

    assert exc.value.exit_code == 1


def test_chatgpt_oauth_prompt_uses_browser_flow(monkeypatch):
    from priests.cli import init_cmd
    from priests.providers.chatgpt_auth import ChatGPTAuthTokens
    from priests.registry import REGISTRY

    monkeypatch.setattr(init_cmd, "_arrow_select", lambda _prompt, _choices: "oauth")
    monkeypatch.setattr(
        init_cmd,
        "authorize_chatgpt_with_browser",
        lambda: ChatGPTAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            api_key="sk-chatgpt-oauth",
            id_token="id-token",
            expires_at=1893456000,
        ),
    )

    credentials = init_cmd._prompt_provider_credentials("chatgpt", REGISTRY["chatgpt"])

    assert credentials.api_key == "sk-chatgpt-oauth"
    assert credentials.oauth_token == "refresh-token"
    assert credentials.api_key_expires_at == 1893456000
    assert credentials.base_url == "https://api.openai.com/v1"


def test_chatgpt_api_key_prompt_still_supported(monkeypatch):
    from priests.cli import init_cmd
    from priests.registry import REGISTRY

    prompts = []

    monkeypatch.setattr(init_cmd, "_arrow_select", lambda _prompt, _choices: "api_key")

    def fake_prompt(label, hide_input=False):
        prompts.append((label, hide_input))
        return "sk-test"

    monkeypatch.setattr(init_cmd.typer, "prompt", fake_prompt)

    credentials = init_cmd._prompt_provider_credentials("chatgpt", REGISTRY["chatgpt"])

    assert credentials.api_key == "sk-test"
    assert credentials.oauth_token == ""
    assert credentials.base_url == "https://api.openai.com/v1"
    assert prompts == [("OpenAI API key", True)]


def test_chatgpt_authorize_url_contains_redirect_and_pkce():
    from priests.providers.chatgpt_auth import PkceCodes, build_chatgpt_authorize_url

    url = build_chatgpt_authorize_url(
        "http://localhost:1455/auth/callback",
        PkceCodes(code_verifier="verifier", code_challenge="challenge"),
        "state-123",
    )

    assert url.startswith("https://auth.openai.com/oauth/authorize?")
    assert "client_id=app_EMoamEEZ73f0CkXaXp7hrann" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" in url
    assert "code_challenge=challenge" in url
    assert "state=state-123" in url


def test_chatgpt_code_exchange_gets_api_key(monkeypatch):
    from priests.providers import chatgpt_auth

    calls = []

    class FakeResponse:
        is_success = True
        status_code = 200
        text = ""

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return FakeResponse(
                {
                    "access_token": "oauth-access",
                    "refresh_token": "refresh-token",
                    "id_token": "id-token",
                    "expires_in": 3600,
                }
            )
        return FakeResponse({"access_token": "sk-chatgpt-oauth"})

    monkeypatch.setattr(chatgpt_auth.httpx, "post", fake_post)

    tokens = chatgpt_auth.exchange_chatgpt_code_for_tokens(
        "auth-code",
        "http://localhost:1455/auth/callback",
        chatgpt_auth.PkceCodes(code_verifier="verifier", code_challenge="challenge"),
        issuer="https://auth.test",
        client_id="client-id",
    )

    assert tokens.access_token == "oauth-access"
    assert tokens.refresh_token == "refresh-token"
    assert tokens.api_key == "sk-chatgpt-oauth"
    assert calls[0][1]["data"]["grant_type"] == "authorization_code"
    assert calls[1][1]["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert calls[1][1]["data"]["requested_token"] == "openai-api-key"
    assert calls[1][1]["data"]["subject_token"] == "id-token"


def test_chatgpt_refresh_uses_json_and_exchanges_id_token(monkeypatch):
    from priests.providers import chatgpt_auth

    calls = []

    class FakeResponse:
        is_success = True
        status_code = 200
        text = ""

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return FakeResponse(
                {
                    "access_token": "new-oauth-access",
                    "refresh_token": "new-refresh-token",
                    "id_token": "new-id-token",
                    "expires_in": 3600,
                }
            )
        return FakeResponse({"access_token": "new-sk-chatgpt-oauth"})

    monkeypatch.setattr(chatgpt_auth.httpx, "post", fake_post)

    tokens = chatgpt_auth.refresh_chatgpt_access_token(
        "old-refresh-token",
        issuer="https://auth.test",
        client_id="client-id",
    )

    assert tokens.access_token == "new-oauth-access"
    assert tokens.refresh_token == "new-refresh-token"
    assert tokens.api_key == "new-sk-chatgpt-oauth"
    assert calls[0][1]["json"]["grant_type"] == "refresh_token"
    assert calls[1][1]["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"


def test_github_copilot_prompt_uses_device_oauth(monkeypatch):
    from priests.cli import init_cmd
    from priests.registry import REGISTRY

    expected = init_cmd.ProviderCredentials(
        api_key="tid=copilot",
        base_url="https://api.githubcopilot.com",
        oauth_token="gho-device",
        api_key_expires_at=1893456000,
    )

    monkeypatch.setattr(init_cmd, "_arrow_select", lambda _prompt, _choices: "device")
    monkeypatch.setattr(init_cmd, "_authorize_github_copilot_device", lambda: expected)

    credentials = init_cmd._prompt_provider_credentials("github_copilot", REGISTRY["github_copilot"])

    assert credentials == expected


def test_apply_provider_to_config_saves_github_copilot_oauth_fields():
    from priests.cli.init_cmd import _apply_provider_to_config
    from priests.config.model import ProvidersConfig

    providers = ProvidersConfig()

    _apply_provider_to_config(
        providers,
        "github_copilot",
        "tid=copilot",
        "",
        base_url="https://api.githubcopilot.com",
        oauth_token="gho-device",
        api_key_expires_at=1893456000,
    )

    assert providers.github_copilot is not None
    assert providers.github_copilot.api_key == "tid=copilot"
    assert providers.github_copilot.oauth_token == "gho-device"
    assert providers.github_copilot.api_key_expires_at == 1893456000
