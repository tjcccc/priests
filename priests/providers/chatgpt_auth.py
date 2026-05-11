from __future__ import annotations

import base64
import hashlib
import html
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


CHATGPT_AUTH_ISSUER = "https://auth.openai.com"
CHATGPT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_OAUTH_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
CHATGPT_OAUTH_PORTS = (1455, 1457)
CHATGPT_CALLBACK_PATH = "/auth/callback"


class ChatGPTOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class PkceCodes:
    code_verifier: str
    code_challenge: str


@dataclass(frozen=True)
class ChatGPTAuthTokens:
    access_token: str
    refresh_token: str
    api_key: str = ""
    id_token: str = ""
    expires_at: int | None = None


def generate_pkce() -> PkceCodes:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return PkceCodes(code_verifier=verifier, code_challenge=challenge)


def build_chatgpt_authorize_url(
    redirect_uri: str,
    pkce: PkceCodes,
    state: str,
    *,
    issuer: str = CHATGPT_AUTH_ISSUER,
    client_id: str = CHATGPT_OAUTH_CLIENT_ID,
) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": CHATGPT_OAUTH_SCOPE,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": "priests_cli",
    }
    return f"{issuer.rstrip('/')}/oauth/authorize?{urlencode(query)}"


def exchange_chatgpt_code_for_tokens(
    code: str,
    redirect_uri: str,
    pkce: PkceCodes,
    *,
    issuer: str = CHATGPT_AUTH_ISSUER,
    client_id: str = CHATGPT_OAUTH_CLIENT_ID,
) -> ChatGPTAuthTokens:
    try:
        response = httpx.post(
            f"{issuer.rstrip('/')}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": pkce.code_verifier,
            },
            timeout=20.0,
        )
    except httpx.RequestError as exc:
        raise ChatGPTOAuthError(f"ChatGPT token exchange failed: {type(exc).__name__}: {exc}") from exc

    if not response.is_success:
        raise ChatGPTOAuthError(
            f"ChatGPT token exchange failed: HTTP {response.status_code}: {response.text}"
        )

    data = response.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token or not refresh_token:
        raise ChatGPTOAuthError("ChatGPT token exchange response did not include access and refresh tokens.")
    id_token = data.get("id_token", "")
    api_value = exchange_chatgpt_id_token_for_api_key(id_token, issuer=issuer, client_id=client_id) if id_token else ""
    expires_in = data.get("expires_in")
    return ChatGPTAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        api_key=api_value,
        id_token=id_token,
        expires_at=int(time.time()) + int(expires_in) if expires_in is not None else None,
    )


def exchange_chatgpt_id_token_for_api_key(
    id_token: str,
    *,
    issuer: str = CHATGPT_AUTH_ISSUER,
    client_id: str = CHATGPT_OAUTH_CLIENT_ID,
) -> str:
    try:
        response = httpx.post(
            f"{issuer.rstrip('/')}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": client_id,
                "requested_token": "openai-api-key",
                "subject_token": id_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
            },
            timeout=20.0,
        )
    except httpx.RequestError as exc:
        raise ChatGPTOAuthError(f"ChatGPT API key exchange failed: {type(exc).__name__}: {exc}") from exc

    if not response.is_success:
        raise ChatGPTOAuthError(f"ChatGPT API key exchange failed: HTTP {response.status_code}: {response.text}")

    data = response.json()
    api_value = data.get("access_token")
    if not api_value:
        raise ChatGPTOAuthError("ChatGPT API key exchange response did not include an access token.")
    return api_value


def refresh_chatgpt_access_token(
    refresh_token: str,
    *,
    issuer: str = CHATGPT_AUTH_ISSUER,
    client_id: str = CHATGPT_OAUTH_CLIENT_ID,
) -> ChatGPTAuthTokens:
    try:
        response = httpx.post(
            f"{issuer.rstrip('/')}/oauth/token",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            timeout=20.0,
        )
    except httpx.RequestError as exc:
        raise ChatGPTOAuthError(f"ChatGPT token refresh failed: {type(exc).__name__}: {exc}") from exc

    if not response.is_success:
        raise ChatGPTOAuthError(f"ChatGPT token refresh failed: HTTP {response.status_code}: {response.text}")

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise ChatGPTOAuthError("ChatGPT token refresh response did not include an access token.")
    next_refresh = data.get("refresh_token") or refresh_token
    id_token = data.get("id_token", "")
    api_value = exchange_chatgpt_id_token_for_api_key(id_token, issuer=issuer, client_id=client_id) if id_token else ""
    expires_in = data.get("expires_in")
    return ChatGPTAuthTokens(
        access_token=access_token,
        refresh_token=next_refresh,
        api_key=api_value,
        id_token=id_token,
        expires_at=int(time.time()) + int(expires_in) if expires_in is not None else None,
    )


class _CallbackState:
    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self.event = Event()
        self.code: str | None = None
        self.error: str | None = None


class _OAuthCallbackServer(ThreadingHTTPServer):
    state: _CallbackState


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != CHATGPT_CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        query = parse_qs(parsed.query)
        callback_state = self.server.state  # type: ignore[attr-defined]
        state = query.get("state", [""])[0]
        if state != callback_state.expected_state:
            callback_state.error = "OAuth callback state mismatch."
            self._respond("Sign-in failed: state mismatch.", status=400)
            callback_state.event.set()
            return

        if error := query.get("error", [""])[0]:
            description = query.get("error_description", [""])[0]
            callback_state.error = description or error
            self._respond(f"Sign-in failed: {callback_state.error}", status=400)
            callback_state.event.set()
            return

        code = query.get("code", [""])[0]
        if not code:
            callback_state.error = "OAuth callback did not include an authorization code."
            self._respond("Sign-in failed: missing authorization code.", status=400)
            callback_state.event.set()
            return

        callback_state.code = code
        self._respond("Sign-in complete. You can close this window and return to priests.")
        callback_state.event.set()

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _respond(self, message: str, status: int = 200) -> None:
        body = (
            "<!doctype html><html><head><title>priests auth</title></head>"
            f"<body><p>{html.escape(message)}</p></body></html>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_chatgpt_callback_server(state: str) -> tuple[_OAuthCallbackServer, str]:
    last_error: OSError | None = None
    for port in CHATGPT_OAUTH_PORTS:
        try:
            server = _OAuthCallbackServer(("127.0.0.1", port), _OAuthCallbackHandler)
            server.state = _CallbackState(state)
            return server, f"http://localhost:{port}{CHATGPT_CALLBACK_PATH}"
        except OSError as exc:
            last_error = exc
    detail = f": {last_error}" if last_error else ""
    raise ChatGPTOAuthError(f"Could not start local OAuth callback server on ports 1455 or 1457{detail}")


def authorize_chatgpt_with_browser(open_browser: bool = True, timeout_seconds: int = 300) -> ChatGPTAuthTokens:
    pkce = generate_pkce()
    state = secrets.token_urlsafe(32)
    server, redirect_uri = start_chatgpt_callback_server(state)
    auth_url = build_chatgpt_authorize_url(redirect_uri, pkce, state)

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if open_browser:
            webbrowser.open(auth_url)
        print("Open this URL in your browser to sign in with ChatGPT:")
        print(auth_url)
        print("Waiting for the browser redirect...")
        if not server.state.event.wait(timeout_seconds):
            raise ChatGPTOAuthError("ChatGPT sign-in timed out before the callback was received.")
        if server.state.error:
            raise ChatGPTOAuthError(server.state.error)
        if not server.state.code:
            raise ChatGPTOAuthError("ChatGPT sign-in did not return an authorization code.")
        return exchange_chatgpt_code_for_tokens(server.state.code, redirect_uri, pkce)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
