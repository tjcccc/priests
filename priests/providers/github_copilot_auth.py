from __future__ import annotations

from dataclasses import dataclass

import httpx

from priests.registry import REGISTRY


GITHUB_COPILOT_HEADERS = {
    "Editor-Version": "priests/0",
    "Editor-Plugin-Version": "priests/0",
    "Copilot-Integration-Id": "vscode-chat",
    "User-Agent": "priests",
}


class GitHubCopilotAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubCopilotToken:
    token: str
    base_url: str
    expires_at: int | None


def looks_like_copilot_ide_token(token: str) -> bool:
    return token.startswith("tid=")


def copilot_api_base_url(data: dict) -> str:
    endpoints = data.get("endpoints")
    if isinstance(endpoints, dict):
        api = endpoints.get("api")
        if isinstance(api, str) and api:
            return api.rstrip("/")
    return REGISTRY["github_copilot"].default_base_url


async def exchange_github_token_for_copilot_token(github_token: str) -> GitHubCopilotToken:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.github.com/copilot_internal/v2/token",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"token {github_token}",
                    "Editor-Version": GITHUB_COPILOT_HEADERS["Editor-Version"],
                    "User-Agent": GITHUB_COPILOT_HEADERS["User-Agent"],
                },
            )
    except httpx.RequestError as exc:
        raise GitHubCopilotAuthError(
            f"Could not exchange GitHub token for Copilot token: {exc}"
        ) from exc

    if response.status_code != 200:
        raise GitHubCopilotAuthError(
            "GitHub Copilot token exchange failed: "
            f"HTTP {response.status_code}: {response.text}"
        )

    data = response.json()
    token = data.get("token") or data.get("copilot_token")
    if not token:
        raise GitHubCopilotAuthError(
            "GitHub Copilot token exchange did not return a token."
        )

    expires_at = data.get("expires_at")
    return GitHubCopilotToken(
        token=token,
        base_url=copilot_api_base_url(data),
        expires_at=int(expires_at) if expires_at is not None else None,
    )
